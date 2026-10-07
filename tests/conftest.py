"""测试公共装置：应用实例、内存 span exporter、日志捕获。"""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.config import Settings
from app.main import create_app
from app.observability.logging import JsonFormatter


@pytest.fixture()
def settings() -> Settings:
    return Settings(trace_sample_rate=1.0)


@pytest.fixture()
def span_exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture()
def app(settings, span_exporter):
    return create_app(settings, span_exporter=span_exporter)


@pytest.fixture()
def client(app) -> TestClient:
    # raise_server_exceptions=False：模拟线上行为，异常返回 500 而非抛出
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def log_records(app):
    """捕获服务日志器输出的结构化日志，逐行解析为 dict。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = app.state.logger
    logger.addHandler(handler)
    try:
        yield lambda: [
            json.loads(line) for line in stream.getvalue().splitlines() if line
        ]
    finally:
        logger.removeHandler(handler)
