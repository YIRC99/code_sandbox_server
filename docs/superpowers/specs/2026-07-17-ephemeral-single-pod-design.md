# Ephemeral Single-Pod Sandbox Design

## Goal

Deploy the sandbox without cluster-managed persistent storage while preserving subprocess isolation. Uploaded code is temporary, but it remains available for repeated execution while the Pod and retention window remain valid.

## Runtime contract

- `/upload` stores a Python file in Pod-local ephemeral storage and returns its date and filename.
- `/execute` resolves the stored file without consuming it. The same upload may be executed repeatedly or concurrently.
- Execution success, script error, timeout, admission rejection, and internal executor errors do not delete the original upload.
- Existing subprocess execution, resource limits, timeout handling, and process-tree cleanup remain unchanged.
- Uploaded files are deleted by the periodic cleanup after 30 days.

## Kubernetes deployment

- The Deployment has exactly one replica and uses the `Recreate` strategy.
- `/data/uploads` and `/tmp` use size-limited `emptyDir` volumes.
- PVC, HPA, and PodDisruptionBudget resources are removed from the active manifests because the service deliberately has no persistent or multi-Pod availability contract.
- Jenkins removes any HPA and PodDisruptionBudget left by an earlier deployment before applying the new manifests, so an old controller cannot scale the Deployment back up. It does not try to delete an old PVC because the deployment credential may not have PVC permissions and the volume is no longer mounted.
- A Pod restart or redeployment discards all uploaded code. This is expected behavior.

## Verification

Automated tests cover repeated and concurrent execution, 30-day retention, the single-Pod manifest, ephemeral upload storage, and the absence of PVC/HPA/PDB resources. The normal Pytest and Ruff checks must pass before pushing.
