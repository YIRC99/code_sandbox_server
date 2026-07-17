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
    assert "USER 10001:10001" in dockerfile
    assert "/data/uploads" in dockerfile


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
