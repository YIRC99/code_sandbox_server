# Jenkins Kustomize Rendering Design

## Goal

Deploy the immutable Jenkins build image by writing the complete image reference into a locally rendered manifest, then verify that manifest before it reaches the cluster.

## Design

- Keep the checked-in Kustomize overlay unchanged.
- Run `kubectl kustomize` to produce the complete manifest containing every managed resource.
- Assert that the manifest contains exactly one current `:latest` image, replace that full image reference with `IMAGE_REF`, then assert exactly one final image before applying the manifest once.
- Delete the temporary manifest through a shell trap.
- Keep rollout diagnostics unchanged.

## Why Bond_Y Does Not Fail This Way

Bond_Y writes a complete `image: registry/project/service:<build>` value into a generated Deployment manifest. That YAML scalar contains the full image reference and is naturally parsed as a string. This project writes only a numeric value into Kustomize's string-typed `newTag` field, which is why unquoted shell substitution becomes an integer.

## Verification

- A pipeline contract test verifies complete rendering, exact source and target image counts, replacement, and apply ordering.
- The contract rejects an additional Python renderer, `kubectl set image` output that would omit unchanged resources, and direct `apply -k` followed by a second cluster mutation.
- The normal test, lint, and format checks must pass.
