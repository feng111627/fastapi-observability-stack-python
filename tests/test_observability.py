"""可观测性能力测试：指标口径、进行中请求数回落、追踪与日志关联。"""

import asyncio
import io
import json
import logging

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from observability import metrics
from observability.config import ObservabilitySettings
from observability.logging import CorrelationFilter, JsonFormatter
from observability.middleware import ObservabilityMiddleware


@pytest.fixture()
def span_exporter():
    return InMemorySpanExporter()


@pytest.fixture()
def app(span_exporter):
    """构造带完整可观测性链路的测试应用。"""
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    tracer = provider.get_tracer("test")

    settings = ObservabilitySettings(service_name="test")
    test_app = FastAPI()
    test_app.add_middleware(
        ObservabilityMiddleware,
        settings=settings,
        tracer=tracer,
        routes=test_app.routes,
    )

    @test_app.get("/ok")
    def ok():
        return {"status": "ok"}

    @test_app.get("/items/{item_id}")
    def get_item(item_id: int):
        return {"item_id": item_id}

    @test_app.get("/boom")
    def boom():
        raise RuntimeError("boom-internal-detail")

    @test_app.get("/trace-id")
    async def trace_id():
        # 跨一次异步切换后再读追踪上下文，验证上下文传播。
        await asyncio.sleep(0)
        ctx = trace.get_current_span().get_span_context()
        return {"trace_id": format(ctx.trace_id, "032x")}

    @test_app.get("/metrics")
    def metrics_endpoint():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return test_app


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def log_stream():
    """给请求日志 logger 挂一个 JSON 输出到内存的 handler。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationFilter())
    logger = logging.getLogger("observability.request")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield stream
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def read_json_logs(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().strip().splitlines()]


def counter_value(route: str, status_class: str, method: str = "GET") -> float:
    return metrics.REQUESTS_TOTAL.labels(
        method=method, route=route, status_class=status_class
    )._value.get()


def inflight_value(route: str) -> float:
    return metrics.REQUESTS_INFLIGHT.labels(route=route)._value.get()


def histogram_count(route: str, method: str = "GET") -> float:
    return metrics.REQUEST_DURATION.labels(method=method, route=route)._sum.get()  # noqa: F841


# ---------- 指标 ----------


def test_request_counter_by_route_and_status(client):
    """请求量按路由模板与结果类别分别计数。"""
    before_ok = counter_value("/ok", "2xx")
    before_4xx = counter_value("/no-such-path", "4xx")

    assert client.get("/ok").status_code == 200
    assert client.get("/ok").status_code == 200
    client.get("/no-such-path")

    assert counter_value("/ok", "2xx") == before_ok + 2
    assert counter_value("/no-such-path", "4xx") == before_4xx + 1


def test_dynamic_path_params_use_route_template(client):
    """动态路径参数收敛到路由模板，不产生无限维度。"""
    before = counter_value("/items/{item_id}", "2xx")
    client.get("/items/1")
    client.get("/items/2")
    client.get("/items/999")
    assert counter_value("/items/{item_id}", "2xx") == before + 3
    # 不应出现以具体参数值为标签的序列
    assert counter_value("/items/1", "2xx") == 0
    assert counter_value("/items/{param}", "2xx") == 0


def test_duration_histogram_observed(client):
    """耗时直方图记录到对应路由上。"""
    metric = metrics.REQUEST_DURATION.labels(method="GET", route="/ok")
    before = metric._sum.get()
    client.get("/ok")
    assert metric._sum.get() > before


def test_metrics_endpoint_not_counted(client):
    """指标采集入口自身流量不混入业务统计。"""
    before = counter_value("/metrics", "2xx")
    assert client.get("/metrics").status_code == 200
    assert counter_value("/metrics", "2xx") == before


# ---------- 进行中请求数 ----------


def test_inflight_returns_to_zero_after_normal_request(client):
    """正常请求结束后，进行中数量回落。"""
    client.get("/ok")
    assert inflight_value("/ok") == 0


def test_inflight_returns_to_zero_after_exception(client):
    """抛异常的请求结束后，进行中数量同样回落，且计入 5xx。"""
    before_5xx = counter_value("/boom", "5xx")
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert inflight_value("/boom") == 0
    assert counter_value("/boom", "5xx") == before_5xx + 1


def test_inflight_returns_to_zero_on_cancellation():
    """请求被取消（CancelledError）时，进行中数量正确回落。"""
    with pytest.raises(asyncio.CancelledError):
        with metrics.track_inflight("/cancelled"):
            assert inflight_value("/cancelled") == 1
            raise asyncio.CancelledError()
    assert inflight_value("/cancelled") == 0


# ---------- 追踪 ----------


def test_exception_marked_as_error_span(client, span_exporter):
    """未捕获异常：span 标记为 ERROR 并携带异常事件。"""
    client.get("/boom")
    spans = span_exporter.get_finished_spans()
    boom_spans = [s for s in spans if s.attributes.get("http.route") == "/boom"]
    assert len(boom_spans) == 1
    span = boom_spans[0]
    assert span.status.status_code == trace.StatusCode.ERROR
    assert span.attributes.get("error.type") == "RuntimeError"
    exc_events = [e for e in span.events if e.name == "exception"]
    assert len(exc_events) == 1
    assert exc_events[0].attributes["exception.type"] == "RuntimeError"


def test_error_response_hides_internal_details(client):
    """500 响应不泄露内部异常细节。"""
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal Server Error"}
    assert "boom-internal-detail" not in resp.text


def test_trace_context_propagates_across_async(client, span_exporter):
    """追踪上下文跨异步调用传递：端点内读到的 trace_id 与导出的一致。"""
    resp = client.get("/trace-id")
    assert resp.status_code == 200
    spans = span_exporter.get_finished_spans()
    target = [s for s in spans if s.attributes.get("http.route") == "/trace-id"]
    assert len(target) == 1
    exported_trace_id = format(target[0].context.trace_id, "032x")
    assert resp.json()["trace_id"] == exported_trace_id


# ---------- 日志与关联 ----------


def test_log_levels_distinguish_outcomes(client, log_stream):
    """正常结束、被拒绝、异常结束在日志里可区分。"""
    client.get("/ok")
    client.get("/no-such-path")
    client.get("/boom")
    logs = read_json_logs(log_stream)
    messages = [entry["message"] for entry in logs]
    assert any("request completed" in m and "status=200" in m for m in messages)
    assert any("request rejected" in m and "status=404" in m for m in messages)
    assert any("request failed" in m and "status=500" in m for m in messages)
    levels = {entry["message"].split(":")[0]: entry["level"] for entry in logs}
    assert levels["request completed"] == "INFO"
    assert levels["request rejected"] == "WARNING"
    assert levels["request failed"] == "ERROR"


def test_request_id_correlates_logs_metrics_and_traces(
    client, log_stream, span_exporter
):
    """同一个 request_id 能串起响应头、日志与追踪。"""
    resp = client.get("/ok")
    request_id = resp.headers["x-request-id"]

    logs = read_json_logs(log_stream)
    entry = next(e for e in logs if e["request_id"] == request_id)
    assert entry["trace_id"] != "-"

    spans = span_exporter.get_finished_spans()
    span = next(s for s in spans if s.attributes.get("request_id") == request_id)
    assert format(span.context.trace_id, "032x") == entry["trace_id"]


# ---------- 既有行为回归 ----------


def test_existing_root_behavior_unchanged():
    """根路径对外行为保持不变。"""
    from main import app as main_app

    main_client = TestClient(main_app)
    resp = main_client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"Hello": "World"}
