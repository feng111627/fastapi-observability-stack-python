"""调用追踪：初始化 TracerProvider 并提供 tracer。

- 采样率由配置控制（ParentBasedTraceIdRatio），父级已采样的链路会被保留。
- 可通过 init_tracing 注入自定义 SpanProcessor（测试用内存导出器，
  线上可接 OTLP 导出器）；默认使用控制台导出器便于本地排查。
"""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanProcessor,
)

_SERVICE_NAME_KEY = "service.name"


def init_tracing(
    service_name: str,
    sample_rate: float = 1.0,
    span_processor: SpanProcessor | None = None,
) -> trace.Tracer:
    """初始化全局 TracerProvider 并返回 tracer。"""
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("sample_rate 必须在 [0.0, 1.0] 之间")
    provider = TracerProvider(
        resource=Resource.create({_SERVICE_NAME_KEY: service_name}),
        sampler=ParentBasedTraceIdRatio(sample_rate),
    )
    if span_processor is None:
        span_processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(span_processor)
    trace.set_tracer_provider(provider)
    return provider.get_tracer(service_name)


def get_tracer() -> trace.Tracer:
    """获取当前全局 tracer（未初始化时返回 no-op tracer）。"""
    return trace.get_tracer(__name__)
