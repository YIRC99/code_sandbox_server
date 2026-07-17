# Jenkins Kustomize Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jenkins render a numeric build tag as a YAML string and validate the exact image before deployment.

**Architecture:** A Python YAML renderer owns the type-sensitive Kustomize mutation. Jenkins renders once to a temporary manifest, checks the resolved image, applies that manifest, and restores the repository overlay.

**Tech Stack:** Jenkins Declarative Pipeline, Python 3.12, PyYAML, kubectl/Kustomize, pytest

---

### Task 1: Add the failing deployment regression tests

**Files:**
- Modify: `tests/test_deployment.py`

- [x] Replace the brittle `sed` source assertion with a subprocess test that runs `scripts/render_kustomization.py` using tag `10` and asserts `isinstance(newTag, str)`.
- [x] Assert Jenkins runs the renderer, runs `kube kustomize`, checks `image: $IMAGE_REF`, then applies the rendered manifest in that order.
- [x] Run `uv run pytest -q tests/test_deployment.py` and confirm failure because the renderer does not exist and Jenkins still uses `sed`.

### Task 2: Implement type-safe rendering and manifest validation

**Files:**
- Create: `scripts/render_kustomization.py`
- Modify: `Jenkinsfile`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [x] Add PyYAML as an explicit development dependency.
- [x] Parse the overlay, find exactly one configured image by name, assign `newTag` from the CLI as a string, and write valid YAML.
- [x] Replace `sed` with the renderer, render to a temporary manifest, assert the exact immutable image reference, then apply that file.
- [x] Back up and restore `kustomization.yaml` and remove temporary files with a shell trap.
- [x] Run the focused deployment tests until they pass.

### Task 3: Verify and publish

**Files:**
- Verify all changed files.

- [x] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `git diff --check`.
- [ ] Commit the complete fix and push the current `master` branch to `origin`.
