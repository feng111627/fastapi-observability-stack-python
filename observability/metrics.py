"""请求指标：请求量、耗时分布、进行中请求数。

口径说明：
- http_requests_total：已结束请求计数，按 method/route/status_class 区分。
- http_request_duration_seconds：请求耗时直方图，按 method/route 区分。
- http_requests_inflight：当前处理中请求数，按 route 区分。
route 一律使用路由模板（如 /items/{item_id}），无法匹配路由时
对路径段做归一化，避免动态参数导致维度无限膨胀。
"""

import re
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "已结束的 HTTP 请求总数",
    ["method", "route", "status_class"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时分布（秒）",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

REQUESTS_INFLIGHT = Gauge(
    "http_requests_inflight",
    "当前处理中的 HTTP 请求数",
    ["route"],
)

_DIGIT_SEGMENT = re.compile(r"^\d+$")
_UUID_SEGMENT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def normalize_route(path: str, placeholder: str = "{param}") -> str:
    """把路径中疑似动态参数的段（纯数字、UUID）替换为占位符。"""
    segments = path.split("/")
    normalized = [
        placeholder if _DIGIT_SEGMENT.match(seg) or _UUID_SEGMENT.match(seg) else seg
        for seg in segments
    ]
    return "/".join(normalized) or "/"


def status_class(status: int) -> str:
    """把状态码收敛为 2xx/4xx/5xx 等类别，避免维度膨胀。"""
    return f"{status // 100}xx"


def record_request(route: str, method: str, status: int, duration_seconds: float) -> None:
    """记录一次完成的请求。"""
    REQUESTS_TOTAL.labels(
        method=method, route=route, status_class=status_class(status)
    ).inc()
    REQUEST_DURATION.labels(method=method, route=route).observe(duration_seconds)


@contextmanager
def track_inflight(route: str):
    """进入时进行中请求数 +1，退出（含异常）时 -1。"""
    REQUESTS_INFLIGHT.labels(route=route).inc()
    try:
        yield
    finally:
        REQUESTS_INFLIGHT.labels(route=route).dec()
