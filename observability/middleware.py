"""ASGI 中间件：为每次请求串联指标、追踪与结构化日志。

- 每次请求生成 request_id，写入 contextvar（跨异步调用传播）、
  追踪 span 属性、响应头 X-Request-ID。
- 指标按路由模板统计（如 /items/{item_id}）；未匹配到路由时退化为
  归一化路径，保证动态参数不会撑爆指标维度。
- 配置在 exclude_paths 中的入口（如 /metrics）不计入业务指标。
- 未捕获异常：span 标记为 ERROR 并记录异常信息，日志记 error，
  对外只返回不含内部细节的 500 响应。
"""

import time
import uuid
from collections.abc import Iterable
from logging import Logger

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from observability import metrics
from observability.config import ObservabilitySettings
from observability.logging import get_logger, request_id_var


def resolve_route_template(scope: Scope, routes: Iterable) -> str | None:
    """在路由表中匹配当前请求，返回路由模板；未命中返回 None。"""
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", None)
    return None


class ObservabilityMiddleware:
    """纯 ASGI 中间件，保证 contextvars 在异步调用间正确传播。"""

    def __init__(
        self,
        app: ASGIApp,
        settings: ObservabilitySettings | None = None,
        tracer: trace.Tracer | None = None,
        logger: Logger | None = None,
        routes: Iterable | None = None,
    ) -> None:
        self.app = app
        self.settings = settings or ObservabilitySettings()
        self.tracer = tracer or trace.get_tracer(__name__)
        self.logger = logger or get_logger("observability.request")
        self.routes = routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            await self._handle_http(scope, receive, send, request_id)
        finally:
            request_id_var.reset(token)

    def _resolve_route(self, scope: Scope) -> str:
        """确定本次请求的路由标签：优先路由模板，否则归一化路径。"""
        if self.routes is not None:
            template = resolve_route_template(scope, self.routes)
            if template:
                return template
        return metrics.normalize_route(
            scope["path"], self.settings.route_param_placeholder
        )

    async def _handle_http(
        self, scope: Scope, receive: Receive, send: Send, request_id: str
    ) -> None:
        method = scope["method"]
        path = scope["path"]
        collect_metrics = path not in self.settings.metrics_exclude_paths
        route = self._resolve_route(scope)

        status_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        start = time.perf_counter()
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
            inflight = (
                metrics.track_inflight(route)
                if collect_metrics
                else _nullcontext()
            )
            with inflight:
                try:
                    await self.app(scope, receive, send_wrapper)
                except Exception as exc:
                    duration = time.perf_counter() - start
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.set_attribute("error.type", type(exc).__name__)
                    if collect_metrics:
                        metrics.record_request(route, method, 500, duration)
                    self.logger.error(
                        "request failed: %s %s route=%s status=500 "
                        "duration=%.4fs error=%s",
                        method, path, route, duration, type(exc).__name__,
                    )
                    await self._send_error_response(send)
                    return

            duration = time.perf_counter() - start
            status = status_holder.get("status", 500)
            span.set_attribute("http.response.status_code", status)
            if status >= 500:
                span.set_status(Status(StatusCode.ERROR))
            if collect_metrics:
                metrics.record_request(route, method, status, duration)
            log = self.logger.warning if status >= 400 else self.logger.info
            log(
                "request %s: %s %s route=%s status=%d duration=%.4fs",
                "rejected" if 400 <= status < 500 else "completed",
                method, path, route, status, duration,
            )

    async def _send_error_response(self, send: Send) -> None:
        """返回不含内部异常细节的 500 响应。"""
        body = b'{"detail":"Internal Server Error"}'
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send(
            {"type": "http.response.body", "body": body, "more_body": False}
        )


class _nullcontext:
    """不采集指标时的空上下文管理器。"""

    def __enter__(self):
        return None

    def __exit__(self, *exc_info):
        return False
