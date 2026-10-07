"""结构化日志：每条日志输出 JSON，并自动带上关联标识。

- request_id：每次请求生成一次，贯穿日志 / 指标 / 追踪。
- trace_id / span_id：来自当前 OpenTelemetry 上下文，可与追踪系统对账。
关联标识通过 contextvars 传递，可跨异步调用正确传播。
"""

import json
import logging
from contextvars import ContextVar

from opentelemetry import trace

# 当前请求的关联标识；由中间件设置，跨异步任务传播。
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class CorrelationFilter(logging.Filter):
    """把关联标识注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx is not None and ctx.trace_id:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = "-"
            record.span_id = "-"
        return True


class JsonFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON。"""

    _FIELDS = ("request_id", "trace_id", "span_id")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in self._FIELDS:
            payload[field_name] = getattr(record, field_name, "-")
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def init_logging(level: int = logging.INFO) -> None:
    """初始化根 logger：JSON 格式 + 关联标识过滤器。"""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """获取 logger。"""
    return logging.getLogger(name)
