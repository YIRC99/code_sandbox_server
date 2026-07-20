# Code Sandbox NodePort Exposure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Code Sandbox through the fixed Kubernetes NodePort `32004`.

**Architecture:** Keep the existing Deployment, selector, service port, target port, and Ingress. Change the existing `code-sandbox` Service from the default ClusterIP behavior to `NodePort`, and lock that contract into the deployment test.

**Tech Stack:** Kubernetes YAML, Kustomize, pytest

---

### Task 1: Add and verify NodePort exposure

**Files:**
- Modify: `tests/test_deployment.py:114-128`
- Modify: `k8s/base/service.yaml:7-14`

- [x] **Step 1: Write the failing test**

Add a focused deployment contract test:

```python
def test_kubernetes_service_exposes_fixed_node_port() -> None:
    service = read("k8s/base/service.yaml")

    assert "type: NodePort" in service
    assert "nodePort: 32004" in service
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_deployment.py::test_kubernetes_service_exposes_fixed_node_port -q`

Expected: FAIL because `type: NodePort` is absent from `k8s/base/service.yaml`.

- [x] **Step 3: Implement the minimal Service change**

Update the Service spec to include:

```yaml
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: code-sandbox
  ports:
    - name: http
      port: 32004
      targetPort: http
      nodePort: 32004
```

- [x] **Step 4: Run the focused test and verify GREEN**

Run: `uv run pytest tests/test_deployment.py::test_kubernetes_service_exposes_fixed_node_port -q`

Expected: `1 passed`.

- [x] **Step 5: Run full verification**

Run:

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Expected: every command exits with code 0.

- [x] **Step 6: Commit only the NodePort implementation**

```powershell
git add -- k8s/base/service.yaml tests/test_deployment.py docs/superpowers/plans/2026-07-20-nodeport-exposure.md
git commit -m "fix: expose sandbox through nodeport"
```
