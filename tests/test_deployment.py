from pathlib import Path

ROOT = Path(__file__).parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def all_kubernetes_yaml() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "k8s").rglob("*.yaml"))


def test_dockerfile_uses_lockfile_and_fixed_non_root_user() -> None:
    dockerfile = read("Dockerfile")

    assert "COPY pyproject.toml uv.lock" in dockerfile
    assert "uv sync --frozen --no-install-project --no-dev" in dockerfile
    assert "ghcr.io/astral-sh/uv" not in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert "ARG UV_VERSION=0.11.21" in dockerfile
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in dockerfile
    assert "https://mirrors.tuna.tsinghua.edu.cn" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "/data/uploads" in dockerfile


def test_jenkins_installs_uv_before_host_verification() -> None:
    jenkinsfile = read("Jenkinsfile")

    install_position = jenkinsfile.index("pip install uv")
    sync_position = jenkinsfile.index("uv sync --frozen --group dev")
    assert install_position < sync_position
    assert 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' in jenkinsfile
    assert "docker build --target test" not in jenkinsfile


def test_jenkins_uses_installed_credentials_binding_steps() -> None:
    jenkinsfile = read("Jenkinsfile")

    assert "docker.withRegistry" not in jenkinsfile
    assert "withKubeConfig" not in jenkinsfile
    assert "withCredentials([usernamePassword(" in jenkinsfile
    assert "withCredentials([file(" in jenkinsfile
    assert "172.16.10.15:31001" in jenkinsfile
    assert "yntrust-dev/code-sandbox" in jenkinsfile


def test_jenkins_applies_one_image_revision_and_prints_rollout_diagnostics() -> None:
    jenkinsfile = read("Jenkinsfile")

    render_position = jenkinsfile.index('newTag: latest/newTag: \\"$IMAGE_TAG\\"')
    apply_position = jenkinsfile.index("apply -k k8s/overlays/prod")
    assert render_position < apply_position
    assert "kubectl set image" not in jenkinsfile
    assert "rollout status deployment/code-sandbox-deployment --timeout=300s" in jenkinsfile
    assert "describe deployment code-sandbox-deployment" in jenkinsfile
    assert "describe pvc code-sandbox-uploads" in jenkinsfile
    assert "describe pod" in jenkinsfile
    assert 'logs "$pod" --all-containers' in jenkinsfile


def test_deployment_has_container_and_pod_security_controls() -> None:
    manifests = all_kubernetes_yaml()

    assert "runAsUser: 10001" in manifests
    assert "runAsNonRoot: true" in manifests
    assert "readOnlyRootFilesystem: true" in manifests
    assert "allowPrivilegeEscalation: false" in manifests
    assert "drop:" in manifests
    assert "- ALL" in manifests
    assert "type: RuntimeDefault" in manifests


def test_deployment_has_resources_probes_and_writable_mounts() -> None:
    manifests = all_kubernetes_yaml()

    assert "requests:" in manifests
    assert "limits:" in manifests
    assert "cpu:" in manifests
    assert "ephemeral-storage:" in manifests
    assert "livenessProbe:" in manifests
    assert "readinessProbe:" in manifests
    assert "mountPath: /data/uploads" in manifests
    assert "mountPath: /tmp" in manifests
    assert "persistentVolumeClaim:" in manifests
    assert "emptyDir:" in manifests
    assert "optional: true" in manifests


def test_kubernetes_adds_shared_storage_service_and_scaling() -> None:
    manifests = all_kubernetes_yaml()

    assert "ReadWriteMany" in manifests
    assert "kind: Service" in manifests
    assert "port: 32004" in manifests
    assert "kind: HorizontalPodAutoscaler" in manifests
    assert "kind: PodDisruptionBudget" in manifests


def test_network_policy_denies_egress_by_default() -> None:
    policy = read("k8s/base/network-policy.yaml")

    assert "kind: NetworkPolicy" in policy
    assert "policyTypes:\n    - Egress" in policy
    assert "egress:" not in policy
