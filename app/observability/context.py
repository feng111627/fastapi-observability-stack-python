"""请求级上下文：跨异步调用传递关联标识。"""

from __future__ import annotations

import contextvars

# 当前请求的关联标识（request id），由中间件写入。
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def get_request_id() -> str:
    """返回当前请求的关联标识，无请求上下文时返回 "-"。"""
    return request_id_ctx.get()
