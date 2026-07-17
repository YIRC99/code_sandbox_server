# Code Sandbox Service

面向内部 AI 平台的 Python 代码运行服务。服务常驻运行，每次请求创建一个全新的受限 Python 子进程，适合执行 AI 生成的较长回测代码。

## 调用流程

1. 调用方提前生成不会重复的随机 `.py` 文件名。
2. 通过 `/upload` 上传文件，服务按 `uploads/YYYY-MM-DD/<filename>.py` 保存。
3. 上传接口返回 `date` 和 `filename`。
4. 使用相同的 `date` 和 `filename` 调用 `/execute`。

代码使用文件上传，而不是放在 JSON 请求体中，可以避免长代码的转义、换行和层级解析问题。服务端不会修改文件名，只会做安全校验并拒绝同名覆盖。

## 本地运行

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync --frozen --group dev
uv run python main.py
```

本地监听 `http://127.0.0.1:32004`。未设置 `SANDBOX_API_KEY` 时，本地业务接口不校验 API Key。

## API

健康检查：

```bash
curl http://127.0.0.1:32004/health
```

上传代码：

```bash
curl -X POST http://127.0.0.1:32004/upload \
  -H "X-API-Key: your-key" \
  -F "file=@random_8f31a2.py;type=text/x-python"
```

响应：

```json
{"date":"2026-07-17","filename":"random_8f31a2.py"}
```

执行代码：

```bash
curl -X POST http://127.0.0.1:32004/execute \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"date":"2026-07-17","filename":"random_8f31a2.py","timeout":10}'
```

响应：

```json
{"stdout":"...","stderr":"","exit_code":0,"status":"success"}
```

`status` 可能为 `success`、`error` 或 `timeout`。脚本非零退出时返回 `error` 和实际退出码；超时返回 `timeout` 和 `-1`。文件不存在为 HTTP 404，重复上传为 409，文件过大为 413，执行队列繁忙为 429。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `SANDBOX_UPLOAD_DIR` | `uploads` | 上传根目录 |
| `SANDBOX_API_KEY` | 空 | 业务接口 API Key；生产环境必须设置 |
| `SANDBOX_MAX_UPLOAD_BYTES` | `10485760` | 单文件最大字节数 |
| `SANDBOX_MAX_OUTPUT_BYTES` | `1048576` | stdout、stderr 各自最多保留的字节数 |
| `SANDBOX_MAX_TIMEOUT_SECONDS` | `30` | 调用方可请求的最大超时 |
| `SANDBOX_MAX_CONCURRENT` | `4` | 每个 Pod 同时运行的脚本数 |
| `SANDBOX_MAX_WAITING` | `20` | 每个 Pod 等待队列长度 |
| `SANDBOX_QUEUE_WAIT_SECONDS` | `5` | 等待执行槽的最长时间 |
| `SANDBOX_RETENTION_DAYS` | `7` | 上传文件保留天数 |
| `SANDBOX_CLEANUP_INTERVAL_SECONDS` | `3600` | 过期文件扫描间隔 |
| `SANDBOX_PROCESS_CPU_SECONDS` | `30` | Linux 子进程 CPU 时间限制 |
| `SANDBOX_PROCESS_MEMORY_BYTES` | `1610612736` | Linux 子进程地址空间限制 |
| `SANDBOX_PROCESS_COUNT_LIMIT` | `32` | Linux 子进程可创建的进程数限制 |
| `SANDBOX_PROCESS_FILE_BYTES` | `20971520` | Linux 子进程可写单文件上限 |
| `SANDBOX_PROCESS_OPEN_FILES` | `64` | Linux 子进程打开文件数限制 |
| `SANDBOX_TERMINATE_GRACE_SECONDS` | `1` | 超时后强制杀进程树前的宽限时间 |

总执行并发约等于 `Pod 数量 × SANDBOX_MAX_CONCURRENT`。队列是每个 Pod 独立的；HPA 会根据 CPU 使用率将 Pod 数量从 2 扩到最多 10。

## Kubernetes 部署

生产清单位于 `k8s/overlays/prod`。部署前需要：

- Nginx Ingress Controller。
- Metrics Server，供 HPA 使用。
- 支持 `ReadWriteMany` 的默认 StorageClass，或者在 `k8s/base/pvc.yaml` 中填写对应的 `storageClassName`。
- 能拉取 `harbor.internal.net` 私有镜像的节点或 `imagePullSecret`。

共享 PVC 可以理解为多个 Pod 共用的上传目录：一个 Pod 保存文件后，另一个 Pod 也能根据日期和文件名执行它。如果集群没有支持 `ReadWriteMany` 的存储，PVC 会一直处于 Pending。

先创建 API Key Secret，示例清单不能直接用于生产：

```bash
kubectl create namespace yunnan-agent-prod --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic code-sandbox-secret \
  --from-literal=api-key='replace-with-a-long-random-value' \
  -n yunnan-agent-prod
kubectl apply -k k8s/overlays/prod
```

部署前需要按实际域名修改 `k8s/base/ingress.yaml`。Kubernetes 清单默认禁止 Pod 主动访问外网；如果回测代码需要通过 `yfinance` 或 HTTP 获取行情，必须由运维按允许的目标地址调整或移除 `network-policy.yaml`，不能直接开放任意出口。

## Jenkins

流水线需要 Jenkins Agent 已安装 `uv`、Docker 和 `kubectl`，并配置：

- Harbor 凭据 ID：`harbor-credentials-id`
- Kubeconfig 凭据 ID：`k8s-config-id`

流水线依次执行依赖同步、Pytest、Ruff、镜像构建与推送、Kustomize apply、指定构建号镜像更新和 rollout 等待。任何阶段失败都会停止部署。

## 安全边界

后端强制校验日期、文件名、API Key、上传大小、超时、并发和输出大小；Linux 中还限制 CPU、内存、进程数、文件大小和打开文件数。超时会终止整个进程树。容器使用非 root 用户、只读根文件系统、删除全部 Linux capabilities，并默认禁止外网访问。

这仍然是“受限子进程沙箱”，适合受控的内部 AI 平台，不是面向完全不可信公网用户的虚拟机级隔离。若以后开放外部用户，需要升级到独立 Pod 配合 gVisor、Kata Containers 或专用沙箱运行时。

## 验证

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker build -t code-sandbox:verify .
kubectl apply --dry-run=client -k k8s/overlays/prod
```
