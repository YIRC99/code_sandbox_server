# Process Sandbox Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current FastAPI prototype into a reproducible, resource-bounded Python execution service that can be deployed by Jenkins to Kubernetes without changing the `date + filename` calling contract.

**Architecture:** A long-running FastAPI service streams uploads into a shared date-based directory and launches one fresh Python subprocess per execution. Focused configuration, storage, admission, and execution modules enforce validation, limits, bounded concurrency, process-tree termination, and safe errors; Kubernetes adds pod-level resource, filesystem, storage, and network controls.

**Tech Stack:** Python 3.12, FastAPI, asyncio subprocesses, Linux rlimits, uv, pytest, Ruff, Docker, Jenkins, Kubernetes/Kustomize.

---

## File map

- `src/sandbox/config.py`: validated environment configuration.
- `src/sandbox/storage.py`: date/name validation, bounded upload persistence, retention cleanup.
- `src/sandbox/admission.py`: bounded per-Pod execution slots and wait queue.
- `src/sandbox/executor.py`: subprocess lifecycle, bounded output, timeout and tree termination.
- `src/sandbox/runner.py`: isolated interpreter entry point that applies Linux rlimits before running user code.
- `src/sandbox/main.py`: FastAPI app factory, auth, routes, lifespan and safe HTTP errors.
- `tests/`: behavior tests for each backend boundary.
- `k8s/base/` and `k8s/overlays/prod/`: reusable Kubernetes resources and production overlay.
- `Dockerfile`, `Jenkinsfile`, `.dockerignore`, `.gitignore`, `pyproject.toml`: reproducible build and CI/CD.
- `README.md`: local use, API contract, deployment, configuration, limits and security boundary.

### Task 1: Reproducible development baseline

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Create: `.dockerignore`
- Track: `uv.lock`

- [ ] **Step 1: Add the test and lint tool configuration**

Add a `dev` dependency group containing `pytest`, `pytest-asyncio`, `ruff`, and `httpx`, plus Ruff and Pytest configuration:

```toml
[dependency-groups]
dev = ["pytest>=8,<9", "pytest-asyncio>=0.25,<1", "ruff>=0.11,<1", "httpx>=0.28,<1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py312"
line-length = 100
```

- [ ] **Step 2: Make the lockfile and build context reproducible**

Stop ignoring `uv.lock`, ignore runtime `uploads/`, and add `.dockerignore` entries for Git state, virtual environments, tests, caches, docs and uploads.

- [ ] **Step 3: Refresh dependencies and verify the baseline**

Run: `uv lock && uv sync --frozen --group dev && uv run ruff check .`

Expected: dependency resolution succeeds; Ruff reports only actionable errors in the original prototype, which will be resolved in later tasks.

- [ ] **Step 4: Commit**

Run: `git add .gitignore .dockerignore pyproject.toml uv.lock && git commit -m "build: add reproducible development toolchain"`

### Task 2: Configuration and storage boundary

**Files:**
- Create: `tests/test_config.py`
- Create: `tests/test_storage.py`
- Create: `src/sandbox/config.py`
- Create: `src/sandbox/storage.py`

- [ ] **Step 1: Write failing configuration and storage tests**

Cover exact ISO dates, safe `.py` basenames, traversal rejection, streaming size limits, exclusive creation, partial-file cleanup, and retention cleanup. The desired API is:

```python
settings = Settings(upload_dir=tmp_path, max_upload_bytes=8)
storage = UploadStorage(settings)
saved = await storage.save(upload_file)
assert saved.date == date.today().isoformat()
assert saved.filename == "random.py"
assert storage.resolve(saved.date, saved.filename).is_file()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_config.py tests/test_storage.py -v`

Expected: collection fails because `sandbox.config` and `sandbox.storage` do not exist.

- [ ] **Step 3: Implement environment configuration**

Create a frozen `Settings` dataclass with explicit parsers for API key, upload directory, upload/output limits, timeout cap, per-Pod concurrency, queue size/wait, retention, and Linux CPU/memory/process/file limits. Reject zero or negative limits during construction.

- [ ] **Step 4: Implement safe storage**

Implement `validate_date`, `validate_filename`, `UploadStorage.save`, `resolve`, and `cleanup_expired`. Use async chunk reads, `xb` exclusive writes, a byte counter, and unlink the partial file on every failed upload.

- [ ] **Step 5: Run tests and confirm GREEN**

Run: `uv run pytest tests/test_config.py tests/test_storage.py -v`

Expected: all configuration and storage tests pass.

- [ ] **Step 6: Commit**

Run: `git add src/sandbox/config.py src/sandbox/storage.py tests/test_config.py tests/test_storage.py && git commit -m "feat: add bounded upload storage"`

### Task 3: Bounded execution admission

**Files:**
- Create: `tests/test_admission.py`
- Create: `src/sandbox/admission.py`

- [ ] **Step 1: Write failing admission tests**

Exercise immediate acquisition, release, one queued waiter, queue overflow, and queue timeout using the interface:

```python
gate = ExecutionGate(max_active=1, max_waiting=1, wait_timeout=0.05)
async with gate.slot():
    assert gate.active == 1
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_admission.py -v`

Expected: collection fails because `sandbox.admission` does not exist.

- [ ] **Step 3: Implement the condition-based gate**

Use `asyncio.Condition` to make `active` and `waiting` changes atomic. Raise `SandboxBusyError` when the waiting queue is full or the configured wait expires, and always release a slot in the async context manager's `finally` block.

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `uv run pytest tests/test_admission.py -v`

Expected: all admission tests pass without sleeps longer than the configured test timeout.

- [ ] **Step 5: Commit**

Run: `git add src/sandbox/admission.py tests/test_admission.py && git commit -m "feat: bound sandbox execution concurrency"`

### Task 4: Resource-bounded subprocess execution

**Files:**
- Create: `tests/test_executor.py`
- Create: `src/sandbox/runner.py`
- Create: `src/sandbox/executor.py`

- [ ] **Step 1: Write failing executor tests**

Create temporary scripts that print output, exit nonzero, sleep past the timeout, and exceed the output cap. Assert this contract:

```python
result = await executor.execute(script_path, timeout=1)
assert result.status == "success"
assert result.exit_code == 0
assert result.stdout == "hello\n"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_executor.py -v`

Expected: collection fails because `sandbox.executor` does not exist.

- [ ] **Step 3: Implement the rlimit runner**

`runner.py` reads limit values from a minimal environment, applies available Linux `resource.setrlimit` controls for CPU, address space, process count, file size and open files, changes into the uploaded script directory, and executes the script with `runpy.run_path`.

- [ ] **Step 4: Implement the async executor**

Launch `sys.executable -I runner.py script.py` in a new process session. Drain stdout and stderr concurrently while retaining only the configured number of bytes. On timeout terminate the full POSIX process group with SIGTERM then SIGKILL, or use `taskkill /T /F` on Windows. Map return code zero to `success`, nonzero to `error`, and timeout to `timeout/-1`.

- [ ] **Step 5: Run tests and confirm GREEN**

Run: `uv run pytest tests/test_executor.py -v`

Expected: success, nonzero, timeout, Unicode replacement and output truncation tests pass.

- [ ] **Step 6: Commit**

Run: `git add src/sandbox/runner.py src/sandbox/executor.py tests/test_executor.py && git commit -m "feat: execute code in resource-bounded subprocesses"`

### Task 5: Hardened FastAPI contract

**Files:**
- Create: `tests/test_api.py`
- Replace: `src/sandbox/main.py`
- Modify: `main.py`

- [ ] **Step 1: Write failing API tests**

Use `create_app(Settings(...))` with a temporary upload directory. Cover health without auth; upload and execute with configured `X-API-Key`; 401 authentication; 400 invalid names/dates; 404 missing files; 409 duplicate upload; 413 oversize upload; 429 busy gate; successful execution; nonzero execution; and absence of traceback or absolute paths in 500 responses.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_api.py -v`

Expected: tests fail because the existing module has no app factory, auth dependency, bounded storage, gate, or safe error mapping.

- [ ] **Step 3: Implement the app factory and lifespan**

Construct settings, storage, executor, and gate once in `create_app`. Start a retention cleanup loop in the lifespan and cancel it cleanly at shutdown. Keep `/health`, `/upload`, and `/execute` paths stable.

- [ ] **Step 4: Implement API validation, auth and safe errors**

Use constant-time API key comparison when configured. Preserve `date` and `filename` upload response fields. Map domain failures to 400/404/409/413/429, cap requested timeout at the configured maximum, log request metadata without code bodies, and return a generic 500 detail for unexpected failures.

- [ ] **Step 5: Run tests and confirm GREEN**

Run: `uv run pytest tests/test_api.py -v`

Expected: all endpoint contract tests pass.

- [ ] **Step 6: Commit**

Run: `git add src/sandbox/main.py main.py tests/test_api.py && git commit -m "feat: expose hardened sandbox API"`

### Task 6: Container and Kubernetes deployment

**Files:**
- Modify: `Dockerfile`
- Create: `k8s/base/kustomization.yaml`
- Create: `k8s/base/configmap.yaml`
- Create: `k8s/base/pvc.yaml`
- Create: `k8s/base/deployment.yaml`
- Create: `k8s/base/service.yaml`
- Create: `k8s/base/ingress.yaml`
- Create: `k8s/base/network-policy.yaml`
- Create: `k8s/base/hpa.yaml`
- Create: `k8s/base/pdb.yaml`
- Create: `k8s/base/secret.example.yaml`
- Create: `k8s/overlays/prod/kustomization.yaml`

- [ ] **Step 1: Add static manifest contract tests**

Create `tests/test_deployment.py` that loads the YAML text and asserts the fixed non-root UID, read-only root filesystem, dropped capabilities, seccomp profile, CPU/memory requests and limits, probes, shared `/data/uploads` mount, writable `/tmp`, Service port 32004, HPA, PDB, and default-deny egress policy are present.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_deployment.py -v`

Expected: failures report missing Kubernetes files and Docker hardening.

- [ ] **Step 3: Fix the Docker build**

Copy both `pyproject.toml` and `uv.lock` before `uv sync --frozen --no-install-project`. Create UID/GID 10001, `/data/uploads`, and `/tmp/sandbox`; copy source with owned files; then run Uvicorn as UID 10001.

- [ ] **Step 4: Add Kustomize resources**

Define a two-replica Deployment with shared RWX PVC, ConfigMap settings, Secret API key reference, resources, probes, security contexts, and emptyDir `/tmp`; add Service, Ingress, HPA, PDB and NetworkPolicy. Keep the storage class unset so the cluster operator can select a compatible RWX provisioner.

- [ ] **Step 5: Run tests and manifest validation**

Run: `uv run pytest tests/test_deployment.py -v`

Run when available: `kubectl kustomize k8s/overlays/prod` and `kubectl apply --dry-run=client -k k8s/overlays/prod`

Expected: static tests pass; kubectl renders valid resources without contacting a cluster for client dry-run.

- [ ] **Step 6: Commit**

Run: `git add Dockerfile k8s tests/test_deployment.py && git commit -m "deploy: add hardened Kubernetes runtime"`

### Task 7: Jenkins pipeline and operator documentation

**Files:**
- Modify: `Jenkinsfile`
- Create: `README.md`

- [ ] **Step 1: Update Jenkins**

Run `uv sync --frozen --group dev`, full Pytest, Ruff check, and Ruff format check before building. Build and push the numbered image, apply the production Kustomize overlay, update `deployment/code-sandbox-deployment` container `code-sandbox`, and wait for rollout.

- [ ] **Step 2: Document the complete contract**

Document local startup, curl upload/execute examples, `X-API-Key`, every environment variable, response statuses, the `date + random filename` contract, PVC requirements, Secret creation, Jenkins credential IDs, deployment commands, cleanup behavior, concurrency calculation, and the fact that process isolation is not a hostile multi-tenant VM boundary.

- [ ] **Step 3: Run the full verification suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`

Expected: all tests pass and both Ruff commands exit zero.

Run when available: `docker build -t code-sandbox:verify .`

Expected: the image builds successfully from the committed lockfile.

- [ ] **Step 4: Commit**

Run: `git add Jenkinsfile README.md && git commit -m "docs: add sandbox deployment and operations guide"`

### Task 8: Final requirement audit

**Files:**
- Modify only files required by discovered verification failures.

- [ ] **Step 1: Re-read the approved design and map every requirement to code or deployment configuration**

Confirm upload protocol stability, backend validation, auth, limits, bounded concurrency, process-tree cleanup, shared storage, retention, Docker reproducibility, Kubernetes security, Jenkins rollout, logs, documentation and automated tests.

- [ ] **Step 2: Run fresh final verification**

Run: `uv run pytest -q`

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

Run available deployment checks and record unavailable tools explicitly.

- [ ] **Step 3: Inspect repository state**

Run: `git status --short --branch && git log --oneline -8`

Expected: only intentional ignored runtime files remain outside Git; implementation and documentation commits are present.

