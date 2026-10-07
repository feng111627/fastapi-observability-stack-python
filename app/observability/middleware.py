"""可观测性 ASGI 中间件：一次请求串联指标、追踪与结构化日志。

要点：
- 路由模板在调用下游前通过 routes 匹配解析，保证 in-flight 等指标
  从请求进入起就有稳定的 route 标签；
- 指标采集入口（metrics_path）自身流量直接透传，不计入业务统计；
- 未捕获异常：在 span 上记录异常并标记失败、日志带异常信息、指标记 500，
  然后原样向上抛出，由 Starlette ServerErrorMiddleware 返回不含内部细节的 500；
- in-flight 计数在 finally 中回落，异常/取消都不会泄漏。
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.observability import context
from app.observability.logging import log_request
from app.observability.metrics import (
    UNMATCHED_ROUTE,
    RequestMetrics,
    monotonic_timer,
)


class ObservabilityMiddleware:
    """纯 ASGI 中间件，包裹整个请求生命周期。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        metrics: RequestMetrics,
        tracer: trace.Tracer,
        logger: logging.Logger,
        routes: Iterable[BaseRoute],
    ) -> None:
        self.app = app
        self.settings = settings
        self.metrics = metrics
        self.tracer = tracer
        self.logger = logger
        self.routes = routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # 采集入口自身流量不计入业务统计
        if path == self.settings.metrics_path:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        route = self._resolve_route(scope)
        request_id = self._extract_request_id(scope)
        token = context.request_id_ctx.set(request_id)

        with self.tracer.start_as_current_span(
            f"{method} {route}",
            kind=trace.SpanKind.SERVER,
            attributes={
                "http.request.method": method,
                "http.route": route,
                "url.path": path,
                "request_id": request_id,
            },
        ) as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            status_code: int | None = None
            timer = monotonic_timer()
            timer.__enter__()
            try:
                with self.metrics.track_in_flight(method, route):

                    async def send_wrapper(message: Message) -> None:
                        nonlocal status_code
                        if message["type"] == "http.response.start":
                            status_code = message["status"]
                            self._inject_response_headers(message, request_id)
                        await send(message)

                    await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                # 未捕获异常：追踪标记失败并记录异常，日志带异常信息，
                # 指标按 500 计，随后原样上抛（对外响应不含内部细节）。
                status_code = 500
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.set_attribute("http.response.status_code", 500)
                self.metrics.observe(method, route, 500, timer.elapsed)
                log_request(
                    self.logger,
                    request_id=request_id,
                    trace_id=trace_id,
                    method=method,
                    route=route,
                    status_code=500,
                    duration_seconds=timer.elapsed,
                    exc_info=True,
                )
                raise
            finally:
                timer.__exit__(None, None, None)
                context.request_id_ctx.reset(token)

            span.set_attribute("http.response.status_code", status_code)
            if status_code is not None and status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            self.metrics.observe(method, route, status_code or 0, timer.elapsed)
            log_request(
                self.logger,
                request_id=request_id,
                trace_id=trace_id,
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=timer.elapsed,
            )

    def _resolve_route(self, scope: Scope) -> str:
        """匹配路由模板；未匹配（如 404）归为 UNMATCHED_ROUTE。"""
        for route in self.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                path = getattr(route, "path", None)
                if path:
                    return path
        return UNMATCHED_ROUTE

    def _extract_request_id(self, scope: Scope) -> str:
        header_name = self.settings.request_id_header.lower().encode()
        for name, value in scope.get("headers", []):
            if name.lower() == header_name:
                return value.decode(errors="replace")
        return uuid.uuid4().hex

    def _inject_response_headers(
        self, message: Message, request_id: str
    ) -> None:
        """把关联标识回写到响应头，便于客户端上报问题时提供。"""
        headers = message.setdefault("headers", [])
        headers.append(
            (self.settings.request_id_header.lower().encode(), request_id.encode())
        )
