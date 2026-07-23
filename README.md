# Code Sandbox Service

面向内部 AI 平台的受控 Python 回测代码执行服务。服务常驻运行，每次请求创建一个全新的受限 Python 子进程，主要用于执行上游 Python AI 服务按严格模板生成并校验的回测代码，包括策略回测、回撤等指标计算。

## 适用范围与调用边界

本服务不是面向用户开放的通用代码上传平台，也不接受用户随意编写或上传任意 Python 代码。实际调用链为“用户前端 → Java 服务 → Python AI 服务 → 本沙盒服务”：前端、Java 服务和 Python AI 服务负责用户身份、业务参数与回测模板约束，本服务位于内网调用链末端，不直接对公网或终端用户开放。

进入本服务的代码应当已经满足固定回测模板和输入格式要求；沙盒端继续通过导入白名单、运行审计、资源限制、执行超时、并发门控和网络策略提供最后一层运行保护。当前安全设计以受控内部调用和严格模板代码为前提，不以执行完全不可信的任意公网代码为目标。

## 调用流程

1. Python AI 服务按照固定回测模板生成并校验代码，同时生成不会重复的随机 `.py` 文件名。
2. Python AI 服务通过 `/upload` 上传模板代码，服务按 `uploads/YYYY-MM-DD/<filename>.py` 保存。
3. 上传接口返回 `date` 和 `filename`。
4. 将 CSV 放入 `SANDBOX_DATA_DIR` 指定的数据根目录。
5. 使用相同的 `date`、`filename` 和 CSV 相对路径 `data_file` 调用 `/execute`。

代码使用文件上传，而不是放在 JSON 请求体中，可以避免长代码的转义、换行和层级解析问题。服务端不会修改文件名，只会做安全校验并拒绝同名覆盖。同一个上传文件可以重复执行，文件超过 30 天后由定时任务清理；Pod 重启或重新部署时也会随 `emptyDir` 一起丢失。

## 本地运行

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync --frozen --group dev
uv run python main.py
```

本地监听 `http://127.0.0.1:32004`。为了方便本地开发，未设置 `SANDBOX_API_KEY` 时业务接口不校验 API Key；部署环境必须设置该值。默认从项目根目录下的 `data` 目录读取 CSV；策略脚本通过 `sys.argv[1]` 获取本次执行的 CSV 路径。

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
  -d '{"date":"2026-07-17","filename":"random_8f31a2.py","data_file":"market/quotes.csv","timeout":10}'
```

响应：

```json
{"stdout":"...","stderr":"","exit_code":0,"status":"success"}
```

`data_file` 必填，只能是相对于数据根目录的 `.csv` 路径，不支持通过接口上传 CSV。绝对路径、越界路径和非 CSV 路径返回 HTTP 400；CSV 或代码文件不存在返回 404。`status` 可能为 `success`、`error` 或 `timeout`。脚本非零退出时返回 `error` 和实际退出码；超时返回 `timeout` 和 `-1`。重复上传为 409，文件过大为 413，执行队列繁忙为 429。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `SANDBOX_UPLOAD_DIR` | `uploads` | 上传根目录 |
| `SANDBOX_DATA_DIR` | `data` | 只读 CSV 数据根目录；配置值会解析为绝对路径 |
| `SANDBOX_API_KEY` | 空 | 业务接口 API Key；生产环境必须设置 |
| `SANDBOX_MAX_UPLOAD_BYTES` | `10485760` | 单文件最大字节数 |
| `SANDBOX_MAX_DATA_FILE_BYTES` | `104857600` | 单个 CSV 数据文件最大字节数 |
| `SANDBOX_MAX_OUTPUT_BYTES` | `10485760` | stdout、stderr 各自最多保留的字节数 |
| `SANDBOX_MAX_TIMEOUT_SECONDS` | `60` | 调用方可请求的最大超时 |
| `SANDBOX_MAX_CONCURRENT` | `4` | 每个 Pod 同时运行的脚本数 |
| `SANDBOX_MAX_WAITING` | `20` | 每个 Pod 等待队列长度 |
| `SANDBOX_QUEUE_WAIT_SECONDS` | `5` | 等待执行槽的最长时间 |
| `SANDBOX_RETENTION_DAYS` | `30` | 上传文件保留天数 |
| `SANDBOX_CLEANUP_INTERVAL_SECONDS` | `3600` | 过期文件扫描间隔 |
| `SANDBOX_PROCESS_CPU_SECONDS` | `60` | Linux 子进程 CPU 时间限制 |

| `SANDBOX_PROCESS_MEMORY_BYTES` | `1610612736` | Linux 子进程地址空间限制 |
| `SANDBOX_PROCESS_COUNT_LIMIT` | `32` | Linux 子进程可创建的进程数限制 |
| `SANDBOX_PROCESS_FILE_BYTES` | `20971520` | Linux 子进程可写单文件上限 |
| `SANDBOX_PROCESS_OPEN_FILES` | `64` | Linux 子进程打开文件数限制 |
| `SANDBOX_TERMINATE_GRACE_SECONDS` | `1` | 超时后强制杀进程树前的宽限时间 |

受当前 Kubernetes 部署条件和上传文件本地性约束，服务固定运行一个 Pod。总执行并发等于 `SANDBOX_MAX_CONCURRENT`，等待队列也只存在于该 Pod 内；这属于当前运行环境下的明确设计约束，不以多 Pod 横向扩展为目标。

## Kubernetes 部署

生产清单位于 `k8s/overlays/prod`。部署前需要：

- Nginx Ingress Controller。
- 能拉取 `harbor.internal.net` 私有镜像的节点或 `imagePullSecret`。

Deployment 固定为一个副本并采用 `Recreate` 更新策略。上传目录和执行临时目录均使用带容量限制的 `emptyDir`，不需要 PVC；Pod 重启或重新部署后，尚未执行的上传文件会丢失，这是预期行为。`Recreate` 会让更新过程出现短暂中断，但能避免新旧 Pod 同时存在时上传和执行请求落到不同 Pod。

生产环境应先创建 API Key Secret；清单允许开发环境在 Secret 不存在时启动，但此时业务接口不会鉴权。示例清单不能直接用于生产：

```bash
kubectl create namespace yntrust-dev --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic code-sandbox-secret \
  --from-literal=api-key='replace-with-a-long-random-value' \
  -n yntrust-dev
kubectl apply -k k8s/overlays/prod
```

部署前需要按实际域名修改 `k8s/base/ingress.yaml`。Kubernetes 清单默认禁止 Pod 主动访问外网；如果回测代码需要通过 `yfinance` 或 HTTP 获取行情，必须由运维按允许的目标地址调整或移除 `network-policy.yaml`，不能直接开放任意出口。

## Jenkins

流水线需要 Jenkins Agent 已安装 Python/pip、Docker 和 `kubectl`。流水线会优先通过清华 PyPI 镜像将 `uv` 安装到 Jenkins 用户目录，失败时回退到 uv 官方安装脚本，然后执行依赖同步、测试和 Ruff。还需要配置：

- Harbor 凭据 ID：`144a6a6f-3dd5-4513-b577-9e1536ad83e3`
- Kubeconfig 凭据 ID：`d99fffce-86d2-4ba7-be11-44bcc2232924`

流水线依次执行依赖同步、Pytest、Ruff、镜像构建与推送、Kustomize apply、指定构建号镜像更新和 rollout 等待。任何阶段失败都会停止部署。

## 安全边界

上游 Python AI 服务负责固定回测模板、业务参数和输入格式校验，不允许终端用户直接上传任意代码。沙盒后端继续校验日期、文件名、CSV 相对路径、API Key、上传大小、超时、并发和输出大小；Linux 中还限制 CPU、内存、进程数、文件大小和打开文件数。每次执行会把代码和选定 CSV 复制到独立临时目录，模板代码正常情况下只操作本次执行副本；该机制用于降低任务之间误修改的风险，不等同于虚拟机级文件系统隔离。原始上传文件保留至超过 30 天或 Pod 被替换，超时会终止整个进程树。容器使用非 root 用户、只读根文件系统、删除全部 Linux capabilities，并默认禁止外网访问。

这仍然是“受限子进程沙箱”，适合当前“固定内网调用链 + 严格回测模板”的业务场景，不是面向完全不可信公网用户的虚拟机级隔离。只要调用边界保持不变，就没有必要为每次简单回测引入独立 Pod 或 MicroVM 的额外启动成本；若以后允许外部用户直接提交任意代码，再升级到独立 Pod、gVisor、Kata Containers 或专用沙箱运行时。

## 验证

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker build -t code-sandbox:verify .
kubectl apply --dry-run=client -k k8s/overlays/prod
```
