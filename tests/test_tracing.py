"""调用追踪测试：span 生成、异常标记、跨异步上下文传播、采样配置。"""

from __future__ import annotations

import asyncio

from opentelemetry.trace import StatusCode

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def finished(spans_exporter):
    return spans_exporter.get_finished_spans()


class TestRequestSpans:
    """单次请求生成可关联的追踪记录。"""

    def test_每个请求生成SERVER_SPAN并带路由属性(self, client, span_exporter):
        client.get("/items/9")
        spans = finished(span_exporter)
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "GET /items/{item_id}"
        assert span.kind.name == "SERVER"
        assert span.attributes["http.route"] == "/items/{item_id}"
        assert span.attributes["http.response.status_code"] == 200
        assert span.attributes["request_id"]
        assert span.status.status_code == StatusCode.UNSET

    def test_被拒绝请求不标记为失败(self, client, span_exporter):
        client.get("/restricted")
        span = finished(span_exporter)[0]
        assert span.attributes["http.response.status_code"] == 403
        assert span.status.status_code != StatusCode.ERROR

    def test_未捕获异常标记失败并记录异常信息(self, client, span_exporter):
        response = client.get("/boom")
        assert response.status_code == 500
        span = finished(span_exporter)[0]
        assert span.status.status_code == StatusCode.ERROR
        events = [e for e in span.events if e.name == "exception"]
        assert events, "异常应作为 span event 记录"
        attrs = events[0].attributes
        assert attrs["exception.type"] == "RuntimeError"
        assert "internal failure detail" in attrs["exception.message"]

    def test_正常响应头带回关联标识(self, client):
        response = client.get("/")
        assert response.headers["x-request-id"]

    def test_异常响应不泄露内部细节(self, client):
        response = client.get("/boom")
        assert response.status_code == 500
        assert "internal failure detail" not in response.text
        assert "Traceback" not in response.text


class TestContextPropagation:
    """追踪上下文跨异步调用传递。"""

    def test_异步子任务中的SPAN归属同一条TRACE(self, app, span_exporter):
        tracer = app.state.tracer

        async def child_demo():
            async def work(name):
                with tracer.start_as_current_span(f"child-{name}"):
                    await asyncio.sleep(0)

            await asyncio.gather(work("a"), work("b"))
            return {"ok": True}

        app.add_api_route("/child-demo", child_demo)
        client = TestClient(app)
        assert client.get("/child-demo").status_code == 200

        spans = finished(span_exporter)
        by_name = {s.name: s for s in spans}
        root = by_name["GET /child-demo"]
        for name in ("child-a", "child-b"):
            child = by_name[name]
            assert child.context.trace_id == root.context.trace_id
            assert child.parent.span_id == root.context.span_id


class TestSampling:
    """采样配置生效。"""

    def test_采样率为零时不导出SPAN(self, span_exporter):
        app = create_app(
            Settings(trace_sample_rate=0.0), span_exporter=span_exporter
        )
        client = TestClient(app)
        client.get("/")
        assert finished(span_exporter) == ()
