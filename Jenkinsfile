pipeline {
    agent any

    environment {
        HARBOR_REGISTRY = '172.16.10.15:31001'
        IMAGE_NAME = 'yntrust-dev/code-sandbox'
        IMAGE_TAG = "${env.BUILD_NUMBER}"
        IMAGE_REF = "${HARBOR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
        LATEST_REF = "${HARBOR_REGISTRY}/${IMAGE_NAME}:latest"
        K8S_NAMESPACE = 'yntrust-dev'
        DOCKER_CREDENTIAL_ID = '144a6a6f-3dd5-4513-b577-9e1536ad83e3'
        K8S_CREDENTIAL_ID = 'd99fffce-86d2-4ba7-be11-44bcc2232924'
        DOCKER_BUILDKIT = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Verify') {
            steps {
                sh '''
                    set -eu
                    if ! command -v uv >/dev/null 2>&1; then
                        pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple --user \
                            || pip3 install uv -i https://pypi.tuna.tsinghua.edu.cn/simple --user \
                            || curl -LsSf https://astral.sh/uv/install.sh | sh
                    fi
                    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
                    uv --version
                    uv sync --frozen --group dev
                    uv run pytest -q
                    uv run ruff check .
                    uv run ruff format --check .
                '''
            }
        }

        stage('Build and Push') {
            steps {
                withCredentials([usernamePassword(credentialsId: env.DOCKER_CREDENTIAL_ID, usernameVariable: 'HARBOR_USER', passwordVariable: 'HARBOR_PASS')]) {
                    sh '''
                        set -eu
                        echo "$HARBOR_PASS" | docker login "$HARBOR_REGISTRY" -u "$HARBOR_USER" --password-stdin
                        docker build -t "$IMAGE_REF" .
                        docker tag "$IMAGE_REF" "$LATEST_REF"
                        docker push "$IMAGE_REF"
                        docker push "$LATEST_REF"
                        docker logout "$HARBOR_REGISTRY"
                    '''
                }
            }
        }

        stage('Deploy') {
            steps {
                withCredentials([file(credentialsId: env.K8S_CREDENTIAL_ID, variable: 'KUBECONFIG')]) {
                    sh '''
                        set -eu
                        kube() {
                            kubectl --kubeconfig="$KUBECONFIG" "$@"
                        }

                        RENDERED_MANIFEST=$(mktemp)
                        cleanup() {
                            rm -f "$RENDERED_MANIFEST"
                        }
                        trap cleanup EXIT

                        SOURCE_IMAGE="$HARBOR_REGISTRY/$IMAGE_NAME:latest"
                        kube kustomize k8s/overlays/prod > "$RENDERED_MANIFEST"
                        SOURCE_MATCH_COUNT=$(grep -Fc "image: $SOURCE_IMAGE" "$RENDERED_MANIFEST" || true)
                        if [ "$SOURCE_MATCH_COUNT" -ne 1 ]; then
                            echo "Expected one rendered source image, found $SOURCE_MATCH_COUNT: $SOURCE_IMAGE" >&2
                            grep -n "image:" "$RENDERED_MANIFEST" >&2 || true
                            exit 1
                        fi

                        sed -i "s|image: $SOURCE_IMAGE|image: $IMAGE_REF|" "$RENDERED_MANIFEST"
                        IMAGE_MATCH_COUNT=$(grep -Fc "image: $IMAGE_REF" "$RENDERED_MANIFEST" || true)
                        if [ "$IMAGE_MATCH_COUNT" -ne 1 ]; then
                            echo "Expected one rendered deployment image, found $IMAGE_MATCH_COUNT: $IMAGE_REF" >&2
                            grep -n "image:" "$RENDERED_MANIFEST" >&2 || true
                            exit 1
                        fi

                        kube apply -f "$RENDERED_MANIFEST"
                        kube annotate deployment/code-sandbox-deployment kubernetes.io/change-cause="Jenkins build $BUILD_NUMBER" --overwrite -n "$K8S_NAMESPACE"

                        if ! kube rollout status deployment/code-sandbox-deployment --timeout=300s -n "$K8S_NAMESPACE"; then
                            echo "=== Deployment rollout failed: diagnostics ==="
                            kube get deployment code-sandbox-deployment -n "$K8S_NAMESPACE" -o wide || true
                            kube describe deployment code-sandbox-deployment -n "$K8S_NAMESPACE" || true
                            kube describe pvc code-sandbox-uploads -n "$K8S_NAMESPACE" || true
                            kube get pods -l app.kubernetes.io/name=code-sandbox -n "$K8S_NAMESPACE" -o wide || true
                            kube get events -n "$K8S_NAMESPACE" --sort-by=.lastTimestamp | tail -100 || true

                            pods=$(kube get pods -l app.kubernetes.io/name=code-sandbox -n "$K8S_NAMESPACE" -o jsonpath='{.items[*].metadata.name}')
                            for pod in $pods; do
                                echo "=== Pod: $pod ==="
                                kube describe pod "$pod" -n "$K8S_NAMESPACE" || true
                                kube logs "$pod" --all-containers --tail=200 -n "$K8S_NAMESPACE" || true
                            done
                            exit 1
                        fi
                    '''
                }
            }
        }
    }

    post {
        success {
            echo "Code Sandbox ${IMAGE_TAG} deployed successfully."
        }
        failure {
            echo 'Build or deployment failed. Check the failing stage output.'
        }
    }
}
