"""OpenTelemetry 追踪初始化。

每个应用实例持有独立的 TracerProvider（不写全局单例），
便于测试注入内存 exporter，也避免重复设置全局 provider 的告警。
span 上下文通过 contextvars 传播，天然跨 await/异步任务传递。
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

from app.config import Settings


def configure_tracing(
    settings: Settings, exporter: SpanExporter | None = None
) -> tuple[TracerProvider, trace.Tracer]:
    """构建 TracerProvider 并返回 (provider, tracer)。

    采样率由 settings.trace_sample_rate 控制（TraceIdRatioBased）。
    exporter 缺省时按环境变量 OBS_TRACES_EXPORTER 决定：
    "console" 输出到标准输出，否则不导出（span 仍会生成，便于后续接入）。
    """
    if exporter is None and os.getenv("OBS_TRACES_EXPORTER") == "console":
        exporter = ConsoleSpanExporter()

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.service_name}),
        sampler=TraceIdRatioBased(settings.trace_sample_rate),
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))

    tracer = provider.get_tracer(settings.service_name)
    return provider, tracer
