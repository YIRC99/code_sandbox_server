# Jenkins Kustomize Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jenkins inject the complete immutable image reference locally and validate it before deployment.

**Architecture:** `kubectl kustomize` renders every managed resource into one temporary manifest. Jenkins requires exactly one current image, replaces it with the complete immutable image reference, verifies exactly one result, and applies the complete manifest once.

**Tech Stack:** Jenkins Declarative Pipeline, kubectl/Kustomize, pytest

---

### Task 1: Replace the rendering contract test

**Files:**
- Modify: `tests/test_deployment.py`

- [x] Assert Jenkins renders all Kustomize resources, checks one source image, replaces it with `IMAGE_REF`, verifies one target image, then runs `apply -f`.
- [x] Assert the pipeline contains no Python renderer, `kubectl set image`, or `apply -k` mutation.
- [x] Run `uv run pytest -q tests/test_deployment.py` and confirm failure because Jenkins still invokes the Python renderer.

### Task 2: Render and replace the complete image reference

**Files:**
- Modify: `Jenkinsfile`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Delete: `scripts/render_kustomization.py`

- [x] Replace the Python renderer and Kustomization backup with a complete `kubectl kustomize` render.
- [x] Require exactly one source image, replace the complete image reference, and require exactly one target image before applying the manifest.
- [x] Remove the renderer file and direct PyYAML development dependency, then refresh `uv.lock`.
- [x] Run the focused deployment tests until they pass.

### Task 3: Verify and publish

**Files:**
- Verify all changed files.

- [x] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `git diff --check`.
- [ ] Commit the complete fix and push the current `master` branch to `origin`.
