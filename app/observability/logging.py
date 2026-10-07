"""结构化 JSON 日志：每条请求日志携带关联标识，可区分正常/被拒绝/异常。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import Settings

#: 请求结果分类
OUTCOME_OK = "ok"            # 2xx / 3xx
OUTCOME_REJECTED = "rejected"  # 4xx，请求被拒绝（参数、权限、不存在等）
OUTCOME_ERROR = "error"      # 5xx 或未捕获异常


def classify_outcome(status_code: int | None) -> str:
    """按状态码归类请求结果；无状态码（异常中断）归为 error。"""
    if status_code is None or status_code >= 500:
        return OUTCOME_ERROR
    if status_code >= 400:
        return OUTCOME_REJECTED
    return OUTCOME_OK


class JsonFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON，保留 extra 字段与异常信息。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 合并 extra 传入的结构化字段
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def configure_logging(settings: Settings) -> logging.Logger:
    """配置并返回服务日志器（幂等：重复调用不叠加 handler）。"""
    logger = logging.getLogger("app")
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def log_request(
    logger: logging.Logger,
    *,
    request_id: str,
    trace_id: str,
    method: str,
    route: str,
    status_code: int | None,
    duration_seconds: float,
    exc_info: Any = None,
) -> None:
    """输出一条请求级结构化日志，outcome 区分 ok/rejected/error。"""
    outcome = classify_outcome(status_code)
    level = (
        logging.ERROR
        if outcome == OUTCOME_ERROR
        else logging.WARNING if outcome == OUTCOME_REJECTED else logging.INFO
    )
    logger.log(
        level,
        "request %s",
        outcome,
        extra={
            "request_id": request_id,
            "trace_id": trace_id,
            "method": method,
            "route": route,
            "status_code": status_code,
            "outcome": outcome,
            "duration_seconds": round(duration_seconds, 6),
        },
        exc_info=exc_info,
    )
