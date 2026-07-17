# 受限子进程代码沙箱加固设计

## 目标

把当前 FastAPI 原型完善为可由 Jenkins 部署到 Kubernetes 的内部代码沙箱服务，为其他 AI 平台运行较长的 Python 回测代码。优先保证低启动延迟，同时通过后端强制限制降低 AI 生成代码带来的风险。

## 已确认的调用协议

1. 调用方通过 `multipart/form-data` 上传 `.py` 文件，避免把长代码放进 JSON 请求体时出现格式和转义问题。
2. 调用方在上传前生成不会重复的随机文件名；服务端保留该文件名，仅做严格安全校验。
3. 服务端按当天日期保存为 `uploads/YYYY-MM-DD/<filename>.py`，返回 `date` 和 `filename`。
4. 调用方使用返回的 `date` 和 `filename` 请求执行，服务端安全拼接路径后运行代码。
5. 现有同步执行响应继续返回 `stdout`、`stderr`、`exit_code` 和 `status`。

## 架构选择

生产环境使用常驻 FastAPI Deployment，不为每次执行创建 Kubernetes Job。每个执行请求创建一个全新的 Python 子进程，执行结束后销毁。该方式比线程更容易限制和终止，也比按请求创建 Pod 延迟更低。

这是一套面向内部 AI 平台的“受限进程沙箱”，不是针对完全不可信公网攻击者的强虚拟化边界。若未来开放给外部不可信用户，应升级到独立 Pod、gVisor、Kata Containers 或专用沙箱运行时。

## 服务组件

- `config`：集中读取环境变量，定义上传目录、鉴权、文件与输出上限、并发数、排队时间及子进程资源限制。
- `storage`：验证日期和文件名、流式保存上传文件、处理同名冲突和过期文件清理。
- `executor`：创建子进程、设置 Linux 资源限制、捕获有限输出、处理超时并终止整个进程组。
- `admission`：使用每 Pod 并发信号量限制同时执行数量；在限定时间内无法获得执行槽时返回系统繁忙。
- `api`：保留 `/health`、`/upload` 和 `/execute`，增加可选 API Key 鉴权、一致的错误响应和可追踪日志。

## 安全边界

后端必须强制执行以下规则，不能依赖前端校验：

- 只接受符合命名规则的 `.py` 文件，拒绝路径分隔符、隐藏路径和非法日期。
- 限制上传体积，采用分块写入，失败时删除不完整文件；已有同名文件不覆盖。
- 子进程使用最小环境变量、独立工作目录和新的进程会话。
- Linux 下限制 CPU 时间、地址空间、生成进程数、打开文件数及可写文件大小。
- 限制 stdout 和 stderr 返回大小，防止无限输出耗尽服务内存。
- 超时后终止整个进程组，确保子进程和孙进程一并清理。
- 容器以非 root 用户运行，根文件系统只读，仅上传卷和临时目录可写。
- Kubernetes NetworkPolicy 默认禁止沙箱 Pod 主动访问外部网络。
- API Key 通过 Kubernetes Secret 注入；健康检查不鉴权，业务接口鉴权。
- 不向客户端返回服务端堆栈、绝对路径或内部异常细节。

前端可以进一步限制生成代码，但只作为辅助防线。

## 并发与扩缩容

每个 Pod 的最大执行并发由环境变量配置，默认值取保守值。请求等待执行槽的时间也可配置；超时后返回 HTTP 429，并带明确错误信息。Kubernetes 为 Pod 设置 CPU、内存 requests/limits，并通过 HPA 按 CPU 使用率扩容。

上传目录挂载 ReadWriteMany 共享 PVC，使上传和执行请求落到不同 API Pod 时仍能读取同一文件。存储类别由部署环境提供，清单不绑定具体厂商。服务定期删除超过保留期的上传文件。

## 状态与错误语义

- 子进程返回码为 `0`：`status=success`。
- 子进程非零返回：`status=error`，保留实际 `exit_code` 和受限 stderr。
- 超时：`status=timeout`、`exit_code=-1`。
- 服务内部执行失败：HTTP 500，响应不包含堆栈。
- 文件不存在返回 404，参数非法返回 400，未授权返回 401，文件过大返回 413，并发队列超时返回 429。

## 构建与部署

- 提交 `uv.lock`，Docker 构建阶段同时复制 `pyproject.toml` 和锁文件，保证 `uv sync --frozen` 可复现。
- 增加开发依赖与 Ruff、Pytest 配置，使 Jenkins 的静态检查命令可直接运行。
- Docker 镜像提前创建并授权上传目录，运行时使用固定非 root UID/GID。
- Kubernetes 资源包含 Namespace 可替换配置、PVC、Deployment、Service、Ingress、NetworkPolicy、HPA、PodDisruptionBudget、ServiceAccount 和 Secret 示例。
- Jenkins 构建、推送带构建号的镜像，应用 K8S 清单，更新镜像并等待 rollout；失败时明确退出。

## 测试与验收

自动化测试覆盖上传、路径校验、同名拒绝、大小限制、鉴权、成功执行、非零退出、超时、输出截断、并发拒绝和进程树清理。配置与存储使用临时目录，避免污染真实上传目录。

验收命令包括：

- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `docker build .`
- `kubectl apply --dry-run=client -f k8s/`

若本机缺少 Docker 或 kubectl，只能将对应项明确记录为未在本机验证，不能声称已通过。

