"""运行时配置：采样率、服务名、指标与日志开关等。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """可观测性相关配置项，均可通过环境变量覆盖。"""

    service_name: str = "fastapi-observability-stack"
    metrics_namespace: str = "fastapi_obs"
    metrics_path: str = "/metrics"
    trace_sample_rate: float = 1.0
    log_level: str = "INFO"
    request_id_header: str = "X-Request-ID"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            service_name=os.getenv("OBS_SERVICE_NAME", cls.service_name),
            metrics_namespace=os.getenv("OBS_METRICS_NAMESPACE", cls.metrics_namespace),
            metrics_path=os.getenv("OBS_METRICS_PATH", cls.metrics_path),
            trace_sample_rate=float(os.getenv("OBS_TRACE_SAMPLE_RATE", "1.0")),
            log_level=os.getenv("OBS_LOG_LEVEL", cls.log_level),
            request_id_header=os.getenv("OBS_REQUEST_ID_HEADER", cls.request_id_header),
        )
