# FastAPI 可观测性服务

在原有 FastAPI 服务上叠加了指标（Metrics）、追踪（Tracing）与结构化日志
（Logging）能力，三者通过关联标识互相打通，用于线上问题排查。所有能力由
`observability` 包提供，经 `ObservabilityMiddleware`（纯 ASGI 中间件）接入，
不改变既有接口的对外行为。

## 快速开始

```bash
uv sync
uv run python main.py        # 启动服务
uv run pytest                # 运行测试（-v 显示各用例通过情况，附覆盖率报告）
```

## 指标口径

指标通过 `/metrics` 以 Prometheus 格式暴露，共三个序列：

| 指标 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `http_requests_total` | Counter | `method`, `route`, `status_class` | 已结束请求总数 |
| `http_request_duration_seconds` | Histogram | `method`, `route` | 请求耗时分布（秒） |
| `http_requests_inflight` | Gauge | `route` | 当前处理中请求数 |

口径约定：

- **`route` 一律使用路由模板**（如 `/items/{item_id}`），路径中的动态参数
  不会成为独立维度。未匹配到路由的请求（如 404）退化为归一化路径
  （纯数字、UUID 段替换为 `{param}`），保证维度不会无限膨胀。
- **`status_class` 收敛为 `2xx` / `4xx` / `5xx`** 等类别，不逐个状态码展开。
- **`/metrics` 采集入口自身的流量不计入业务指标**（由
  `metrics_exclude_paths` 配置控制），避免自采集污染统计。
- `http_requests_inflight` 在进入请求时 +1，结束时 -1；即使请求抛异常或
  被取消（`CancelledError`），也会在 `finally` 中正确回落。

## 日志与追踪的关联方式

每次请求生成一个 `request_id`（UUID hex），它是串联三种信号的钥匙：

- **日志**：每条请求日志为单行 JSON，字段包含 `request_id`、`trace_id`、
  `span_id`。`request_id` 通过 `contextvars` 传递，跨异步调用（`await`、
  子任务）保持正确。
- **追踪**：每个请求生成一个 SERVER span，属性中携带 `request_id`，
  `trace_id` 与日志中的 `trace_id` 一致。
- **响应**：响应头携带 `X-Request-ID`，客户端报错时可直接提供该标识，
  用它检索日志（`request_id`）并定位对应 trace（`trace_id`）。

日志按请求结果分级，便于区分：

| 结果 | 级别 | 消息前缀 |
| --- | --- | --- |
| 正常结束（< 400） | INFO | `request completed` |
| 被拒绝（4xx） | WARNING | `request rejected` |
| 异常结束（未捕获异常 / 5xx） | ERROR | `request failed` |

未捕获异常的处理：span 标记为 `ERROR` 并记录异常事件（类型与堆栈），
日志记 ERROR；**对外只返回 `{"detail": "Internal Server Error"}` 的 500
响应，不泄露内部异常细节**。

## 配置项

集中在 `observability.config.ObservabilitySettings`：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `service_name` | `fastapi-observability-stack` | 追踪资源中的服务名 |
| `trace_sample_rate` | `1.0` | 追踪采样率 `[0.0, 1.0]`，采用 `ParentBasedTraceIdRatio`：父级已采样的链路会被保留 |
| `metrics_exclude_paths` | `{"/metrics"}` | 不计入业务指标的路径集合 |
| `route_param_placeholder` | `"{param}"` | 未匹配路由时动态路径段的占位符 |

追踪导出器默认输出到控制台（便于本地排查）；生产环境调用
`observability.tracing.init_tracing(..., span_processor=...)` 注入 OTLP 等
导出器即可。测试中使用 `InMemorySpanExporter` 验证 span 内容。

## 项目结构

```
main.py                      # 应用入口：装配中间件与 /metrics
observability/
  config.py                  # 配置项
  metrics.py                 # Prometheus 指标与路由归一化
  tracing.py                 # TracerProvider 初始化与采样
  logging.py                 # JSON 结构化日志与关联标识注入
  middleware.py              # ASGI 中间件，串联三种信号
tests/test_observability.py  # 指标口径 / inflight 回落 / 追踪与日志关联测试
```
