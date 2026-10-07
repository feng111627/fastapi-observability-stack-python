"""可观测性相关配置项。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ObservabilitySettings:
    """指标 / 追踪 / 日志的采集配置。"""

    service_name: str = "fastapi-observability-stack"
    # 追踪采样率，取值 [0.0, 1.0]
    trace_sample_rate: float = 1.0
    # 不参与业务指标统计的路径（如指标采集入口自身）
    metrics_exclude_paths: frozenset[str] = field(
        default_factory=lambda: frozenset({"/metrics"})
    )
    # 动态路径参数占位符，用于收敛指标维度
    route_param_placeholder: str = "{param}"
