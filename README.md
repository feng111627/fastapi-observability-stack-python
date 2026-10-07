# FastAPI Observability Stack

一个带完整可观测性能力的 FastAPI 服务：指标（Prometheus）、调用追踪（OpenTelemetry）、
结构化日志（JSON），三者通过关联标识串起同一次请求，便于线上排查问题。

## 运行

```bash
uv sync
uv run python main.py          # 或 uv run uvicorn app.main:app
```

## 测试

```bash
uv run pytest                  # 默认 -v，逐条显示覆盖的业务场景与通过情况
```

## 关联方式：一次请求如何串起指标、追踪和日志

每个进入的请求都会被分配一个 **request id**：

- 客户端可用 `X-Request-ID` 请求头指定；未指定时服务端生成（UUID hex）；
- 正常响应会带回 `X-Request-ID` 响应头，用户报障时提供它即可定位；
- 每条请求日志都包含 `request_id` 和 `trace_id` 两个字段：
  - 用 `request_id` 检索日志，可找到该次请求的全部日志；
  - 用日志里的 `trace_id` 到追踪系统里可查出同一次请求的完整 span 链路；
  - 指标按 `route` 标签聚合，与日志/追踪里的 `http.route` 一致，可对照同一接口的
    请求量、耗时分布与具体某次请求的细节。

关联标识通过 `contextvars` 保存，跨 `await` / `asyncio.gather` 等异步调用自动传递；
追踪上下文同理（OpenTelemetry 基于 contextvars 传播），异步子任务中创建的 span
会挂在同一条 trace 下。

## 指标口径

采集入口：`GET /metrics`（Prometheus 文本格式）。该端点自身的流量**不计入**业务统计。

| 指标 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `fastapi_obs_requests_total` | Counter | `method`, `route`, `status` | 请求总量，按方法与结果状态码区分 |
| `fastapi_obs_request_duration_seconds` | Histogram | `method`, `route` | 请求耗时分布 |
| `fastapi_obs_requests_in_flight` | Gauge | `method`, `route` | 当前正在处理中的请求数 |

口径约定：

- `route` 使用**路由模板**（如 `/items/{item_id}`），路径中的动态参数不会展开，
  指标维度不会随参数取值膨胀；未匹配到任何路由的请求（404）统一归为
  `route="unmatched"`；
- `status` 为响应状态码（`200`/`403`/`500`…）；未捕获异常按 `500` 计；
- `in_flight` 在进入请求时 +1、离开（含异常、被取消）时 -1，不会泄漏。

## 追踪

- 每个请求生成一个 SERVER 类型的 span，命名为 `<METHOD> <route模板>`，
  属性含 `http.request.method`、`http.route`、`http.response.status_code`、`request_id`；
- 未捕获异常会在 span 上记录 `exception` 事件（异常类型与信息）并把状态置为 `ERROR`；
  4xx/3xx 不算失败（遵循 OTel 服务端约定）；
- 异常详情只进入追踪与日志，**对外响应不泄露内部异常细节**（仍为统一的 500 响应）。

## 日志

单行 JSON，每条请求日志包含：`timestamp`、`level`、`request_id`、`trace_id`、
`method`、`route`、`status_code`、`outcome`、`duration_seconds`。

`outcome` 把请求结果分为三类，便于按结果过滤：

- `ok`：2xx/3xx，正常结束（INFO）；
- `rejected`：4xx，请求被拒绝（WARNING）；
- `error`：5xx 或未捕获异常（ERROR，且带 `exception` 堆栈字段）。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OBS_SERVICE_NAME` | `fastapi-observability-stack` | 服务名（trace resource / 应用标题） |
| `OBS_METRICS_NAMESPACE` | `fastapi_obs` | 指标名前缀 |
| `OBS_METRICS_PATH` | `/metrics` | 指标采集入口路径（该路径不计入业务统计） |
| `OBS_TRACE_SAMPLE_RATE` | `1.0` | 追踪采样率（0~1，TraceIdRatioBased） |
| `OBS_TRACES_EXPORTER` | （空） | 设为 `console` 时把 span 打印到标准输出；默认不导出 |
| `OBS_LOG_LEVEL` | `INFO` | 日志级别 |
| `OBS_REQUEST_ID_HEADER` | `X-Request-ID` | 关联标识使用的请求/响应头名 |

## 目录结构

```
app/
  config.py                 # 配置项
  main.py                   # 应用工厂与业务路由
  observability/
    context.py              # request id 的 contextvar
    metrics.py              # 指标定义与路由模板归一
    tracing.py              # TracerProvider 初始化与采样
    logging.py              # JSON 日志与结果分类
    middleware.py           # 串联三者的 ASGI 中间件
tests/                      # 指标/追踪/日志/行为保持测试
```
