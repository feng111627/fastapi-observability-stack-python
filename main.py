import uvicorn
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from observability.config import ObservabilitySettings
from observability.logging import init_logging
from observability.middleware import ObservabilityMiddleware
from observability.tracing import init_tracing

settings = ObservabilitySettings()

init_logging()
tracer = init_tracing(settings.service_name, settings.trace_sample_rate)

app = FastAPI()
app.add_middleware(
    ObservabilityMiddleware,
    settings=settings,
    tracer=tracer,
    routes=app.routes,
)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/metrics")
def metrics_endpoint():
    """Prometheus 指标采集入口（不计入业务指标统计）。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
