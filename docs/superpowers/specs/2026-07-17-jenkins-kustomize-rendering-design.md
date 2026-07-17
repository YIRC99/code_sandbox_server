# Jenkins Kustomize Rendering Design

## Goal

Deploy the immutable Jenkins build image without relying on Groovy/Shell/sed quote escaping, and verify the fully rendered Kubernetes manifest before it reaches the cluster.

## Design

- Keep the existing numeric Harbor tag (`BUILD_NUMBER`).
- Add a small Python renderer that parses `kustomization.yaml` as YAML and assigns `images[].newTag` as a Python string.
- In Jenkins, back up the checked-in overlay, invoke the renderer, run `kubectl kustomize` into a temporary manifest, and assert that the manifest contains exactly `IMAGE_REF` before applying it.
- Restore the checked-in overlay and delete temporary files through a shell trap.
- Keep rollout diagnostics unchanged.

## Why Bond_Y Does Not Fail This Way

Bond_Y writes a complete `image: registry/project/service:<build>` value into a generated Deployment manifest. That YAML scalar contains the full image reference and is naturally parsed as a string. This project writes only a numeric value into Kustomize's string-typed `newTag` field, which is why unquoted shell substitution becomes an integer.

## Verification

- A regression test invokes the real renderer with tag `10` and verifies that YAML reloads it as the string `"10"`.
- A pipeline contract test verifies render, image assertion, and apply ordering, and rejects `sed`-based mutation.
- The normal test, lint, and format checks must pass.
