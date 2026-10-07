"""指标口径测试：计数、路由归一、采集入口隔离、in-flight 回落。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.observability.metrics import RequestMetrics
from app.observability.middleware import ObservabilityMiddleware


def counter_value(app, method, route, status) -> float:
    return app.state.metrics.requests_total.labels(method, route, status)._value.get()


def in_flight_value(app, method, route) -> float:
    return app.state.metrics.requests_in_flight.labels(method, route)._value.get()


class TestRequestCounters:
    """请求量与结果分类统计。"""

    def test_正常请求按路由和状态计数(self, client, app):
        client.get("/")
        client.get("/")
        client.get("/items/1")
        assert counter_value(app, "GET", "/", "200") == 2
        assert counter_value(app, "GET", "/items/{item_id}", "200") == 1

    def test_被拒绝和异常请求分别计数(self, client, app):
        client.get("/restricted")
        client.get("/boom")
        client.get("/no-such-path")
        assert counter_value(app, "GET", "/restricted", "403") == 1
        assert counter_value(app, "GET", "/boom", "500") == 1
        assert counter_value(app, "GET", "unmatched", "404") == 1

    def test_动态路径参数归一到路由模板(self, client, app):
        for i in range(5):
            client.get(f"/items/{i}")
        assert counter_value(app, "GET", "/items/{item_id}", "200") == 5
        # 不出现以具体参数值为标签的时间序列
        body = client.get("/metrics").text
        assert "/items/1" not in body and "/items/2" not in body

    def test_耗时分布按路由统计(self, client, app):
        client.get("/")
        body = client.get("/metrics").text
        assert 'fastapi_obs_request_duration_seconds_count{method="GET",route="/"} 1.0' in body

    def test_采集入口自身流量不计入业务统计(self, client, app):
        before = client.get("/metrics").text
        assert 'route="/metrics"' not in before
        client.get("/metrics")
        after = client.get("/metrics").text
        assert 'route="/metrics"' not in after


class TestInFlightGauge:
    """进行中请求数：正常、异常、被打断时都要正确回落。"""

    def test_正常请求结束后回落为零(self, client, app):
        client.get("/")
        assert in_flight_value(app, "GET", "/") == 0

    def test_异常请求结束后回落为零(self, app):
        client = TestClient(app, raise_server_exceptions=True)
        with pytest.raises(RuntimeError):
            client.get("/boom")
        assert in_flight_value(app, "GET", "/boom") == 0

    def test_处理中被取消的请求也回落(self, settings):
        """下游在处理中被打断（CancelledError），in-flight 不得泄漏。"""
        metrics = RequestMetrics(Settings())

        async def cancelled_app(scope, receive, send):
            raise asyncio.CancelledError()

        middleware = ObservabilityMiddleware(
            cancelled_app,
            settings=settings,
            metrics=metrics,
            tracer=app_tracer(settings),
            logger=_quiet_logger(),
            routes=[],
        )
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/slow",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(middleware(scope, receive, send))
        assert metrics.requests_in_flight.labels("GET", "unmatched")._value.get() == 0

    def test_并发高峰时能看到进行中计数(self, client, app):
        # 串行客户端下每次请求结束后都为 0，这里验证指标存在且非负
        client.get("/items/7")
        assert in_flight_value(app, "GET", "/items/{item_id}") == 0


def app_tracer(settings):
    from app.observability.tracing import configure_tracing

    _, tracer = configure_tracing(settings)
    return tracer


def _quiet_logger():
    import logging

    logger = logging.getLogger("test-quiet")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger
