"""结构化日志测试：关联标识、结果分类、异常标记、跨异步上下文。"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.observability.context import get_request_id


class TestCorrelation:
    """日志与响应、追踪之间的关联。"""

    def test_请求日志带关联标识且与响应头一致(self, client, log_records):
        response = client.get("/")
        records = log_records()
        assert len(records) == 1
        record = records[0]
        assert record["request_id"] == response.headers["x-request-id"]
        assert record["trace_id"] and record["trace_id"] != "-"
        assert record["route"] == "/"
        assert record["status_code"] == 200

    def test_日志trace_id与导出的SPAN一致(self, client, span_exporter, log_records):
        client.get("/items/3")
        record = log_records()[0]
        span = span_exporter.get_finished_spans()[0]
        assert record["trace_id"] == format(span.context.trace_id, "032x")

    def test_客户端传入的关联标识被沿用(self, client, log_records):
        response = client.get("/", headers={"X-Request-ID": "req-abc-123"})
        assert response.headers["x-request-id"] == "req-abc-123"
        assert log_records()[0]["request_id"] == "req-abc-123"

    def test_异步处理中能取到同一关联标识(self, app, log_records):
        async def whoami():
            await asyncio.sleep(0)
            return {"request_id": get_request_id()}

        app.add_api_route("/whoami", whoami)
        client = TestClient(app)
        response = client.get("/whoami")
        assert response.json()["request_id"] == response.headers["x-request-id"]


class TestOutcomeClassification:
    """正常、被拒绝、异常结束在日志中可区分。"""

    def test_正常结束记为ok(self, client, log_records):
        client.get("/")
        record = log_records()[0]
        assert record["outcome"] == "ok"
        assert record["level"] == "INFO"

    def test_被拒绝记为rejected(self, client, log_records):
        client.get("/restricted")
        client.get("/no-such-path")
        records = log_records()
        assert records[0]["outcome"] == "rejected"
        assert records[0]["status_code"] == 403
        assert records[0]["level"] == "WARNING"
        assert records[1]["outcome"] == "rejected"
        assert records[1]["status_code"] == 404

    def test_异常结束记为error并带异常堆栈(self, client, log_records):
        client.get("/boom")
        record = log_records()[0]
        assert record["outcome"] == "error"
        assert record["level"] == "ERROR"
        assert record["status_code"] == 500
        assert "RuntimeError" in record["exception"]
        assert "internal failure detail" in record["exception"]

    def test_日志为单行JSON且字段齐全(self, client, log_records):
        client.get("/items/5")
        record = log_records()[0]
        for key in (
            "timestamp",
            "level",
            "message",
            "request_id",
            "trace_id",
            "method",
            "route",
            "status_code",
            "outcome",
            "duration_seconds",
        ):
            assert key in record, f"缺少字段 {key}"
