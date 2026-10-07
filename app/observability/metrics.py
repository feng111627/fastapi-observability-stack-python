"""Prometheus 指标定义与路由标签解析。"""

from __future__ import annotations

import time
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from app.config import Settings

#: 未匹配到任何路由的请求统一归到这个标签值，避免原始路径造成维度膨胀。
UNMATCHED_ROUTE = "unmatched"


class RequestMetrics:
    """请求指标集合：计数、耗时分布、进行中数量。

    标签口径：
    - method: HTTP 方法；
    - route:  路由模板（如 /items/{item_id}），未匹配路由归为 "unmatched"；
    - status: 响应状态码（如 "200"、"404"、"500"）。
    """

    def __init__(
        self, settings: Settings, registry: CollectorRegistry | None = None
    ) -> None:
        ns = settings.metrics_namespace
        self.registry = registry or CollectorRegistry()
        self.requests_total = Counter(
            f"{ns}_requests_total",
            "Total HTTP requests.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.request_duration_seconds = Histogram(
            f"{ns}_request_duration_seconds",
            "HTTP request duration in seconds.",
            ["method", "route"],
            registry=self.registry,
        )
        self.requests_in_flight = Gauge(
            f"{ns}_requests_in_flight",
            "HTTP requests currently being processed.",
            ["method", "route"],
            registry=self.registry,
        )

    def track_in_flight(self, method: str, route: str) -> "_InFlight":
        """进入时 +1，退出（含异常）时 -1，保证进行中数量正确回落。"""
        return _InFlight(self.requests_in_flight.labels(method, route))

    def observe(
        self, method: str, route: str, status: int, duration_seconds: float
    ) -> None:
        self.requests_total.labels(method, route, str(status)).inc()
        self.request_duration_seconds.labels(method, route).observe(
            duration_seconds
        )


class _InFlight:
    def __init__(self, gauge) -> None:
        self._gauge = gauge

    def __enter__(self) -> "_InFlight":
        self._gauge.inc()
        return self

    def __exit__(self, *exc_info) -> None:
        self._gauge.dec()


def resolve_route_template(scope: dict, raw_path: str) -> str:
    """把请求路径归一到路由模板，避免动态参数导致维度膨胀。

    需在路由匹配完成之后（如响应开始时）调用；未匹配路由返回 UNMATCHED_ROUTE。
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return UNMATCHED_ROUTE


def monotonic_timer() -> "_Timer":
    """极简计时器：with 结束后读 .elapsed。"""
    return _Timer()


class _Timer:
    def __enter__(self) -> "_Timer":
        self._start = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc_info) -> None:
        self.elapsed = time.perf_counter() - self._start
