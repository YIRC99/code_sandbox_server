# Ephemeral Single-Pod Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace shared persistent uploads with 30-day Pod-local uploads in a fixed single-Pod deployment.

**Architecture:** Keep the current two-request upload/execute API and subprocess executor. Resolve uploads without consuming them, retain them for 30 days, and let Kubernetes mount a size-limited `emptyDir` in one Pod with `Recreate` updates.

**Tech Stack:** Python 3.12, FastAPI, Pytest, Kubernetes/Kustomize, Jenkins, Ruff.

---

### Task 1: Preserve repeatable execution behavior

**Files:**
- Modify: `tests/test_api.py`
- Modify: `src/sandbox/main.py`
- Modify: `src/sandbox/config.py`
- Modify: `k8s/base/configmap.yaml`

- [ ] Add API tests proving the same upload can be executed repeatedly and concurrently.
- [ ] Add an API test assertion that an internal executor failure preserves the upload.
- [ ] Add configuration and deployment tests requiring a 30-day default retention period.
- [ ] Run the focused tests and confirm they fail against the previous execution restriction and 7-day retention behavior.
- [ ] Resolve uploads without consuming them and set the application and Kubernetes retention defaults to 30 days.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Replace shared Kubernetes storage with ephemeral single-Pod storage

**Files:**
- Modify: `tests/test_deployment.py`
- Modify: `k8s/base/deployment.yaml`
- Modify: `k8s/base/kustomization.yaml`
- Delete: `k8s/base/pvc.yaml`
- Delete: `k8s/base/hpa.yaml`
- Delete: `k8s/base/pdb.yaml`
- Modify: `Jenkinsfile`

- [ ] Update deployment tests to require one replica, `Recreate`, an upload `emptyDir`, and no PVC/HPA/PDB resources.
- [ ] Run the focused deployment tests and confirm they fail against the shared multi-Pod manifests.
- [ ] Apply the minimal manifest changes and remove obsolete PVC rollout diagnostics.
- [ ] Delete any legacy HPA and PDB in Jenkins before applying the rendered manifests.
- [ ] Run the focused deployment tests and confirm they pass.

### Task 3: Update the operator contract and verify

**Files:**
- Modify: `README.md`

- [ ] Document repeatable uploads, 30-day cleanup, Pod-local storage, single-replica operation, and expected data loss on Pod replacement.
- [ ] Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`.
- [ ] Inspect the final Git diff, commit only the scoped tracked files, and push the current branch.
