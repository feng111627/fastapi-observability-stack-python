"""应用工厂：组装业务路由与可观测性中间件。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import Settings
from app.observability.logging import configure_logging
from app.observability.metrics import RequestMetrics
from app.observability.middleware import ObservabilityMiddleware
from app.observability.tracing import configure_tracing


def create_app(settings: Settings | None = None, **observability_overrides) -> FastAPI:
    """构建应用。

    observability_overrides 仅供测试注入（如内存 span exporter）。
    """
    settings = settings or Settings.from_env()
    app = FastAPI(title=settings.service_name)
    app.state.settings = settings

    metrics = RequestMetrics(settings)
    _, tracer = configure_tracing(
        settings, exporter=observability_overrides.get("span_exporter")
    )
    logger = configure_logging(settings)
    app.state.metrics = metrics
    app.state.tracer = tracer
    app.state.logger = logger

    # ---- 业务路由 ----

    @app.get("/")
    def read_root():
        return {"Hello": "World"}

    @app.get("/items/{item_id}")
    async def get_item(item_id: int):
        # 模拟一次跨异步调用的下游处理，验证追踪上下文传播
        await asyncio.sleep(0)
        return {"item_id": item_id}

    @app.get("/restricted")
    def restricted():
        raise HTTPException(status_code=403, detail="forbidden")

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal failure detail")

    # ---- 指标采集入口（不计入业务统计） ----

    @app.get(settings.metrics_path, include_in_schema=False)
    def metrics_endpoint():
        return Response(
            generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST
        )

    app.add_middleware(
        ObservabilityMiddleware,
        settings=settings,
        metrics=metrics,
        tracer=tracer,
        logger=logger,
        routes=app.routes,
    )
    return app


app = create_app()
