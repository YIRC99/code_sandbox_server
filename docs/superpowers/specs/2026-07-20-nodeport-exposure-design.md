# Code Sandbox NodePort 暴露设计

## 目标

让 K8s 部署后的 Code Sandbox 可以通过任意集群节点的 `32004` 端口访问，与 Bond 服务的 `32001` 暴露方式保持一致。

## 设计

- 将 `code-sandbox` Service 显式设置为 `type: NodePort`。
- Service 端口、容器目标端口和固定节点端口统一使用 `32004`。
- 保留现有 selector、Deployment、Ingress 和应用监听配置。
- Jenkins 继续应用现有 Kustomize 清单，无需增加额外部署资源。

## 验收

- 部署清单测试必须校验 `type: NodePort` 和 `nodePort: 32004`。
- 现有测试、Ruff 检查和格式检查通过。
- 集群部署后可通过 `http://<节点IP>:32004/health` 验证访问；实际集群验证由部署环境完成。

## 边界

`32004` 必须未被集群中其他 NodePort Service 占用。若已占用，Kubernetes 会在应用清单时明确报冲突，需要更换端口。
