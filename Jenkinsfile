pipeline {
    agent any

    environment {
        HARBOR_REGISTRY = 'harbor.internal.net'
        IMAGE_NAME = 'yunnan-agent/code-sandbox'
        IMAGE_TAG = "${env.BUILD_NUMBER}"
        K8S_NAMESPACE = 'yunnan-agent-prod'
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
                script {
                    docker.withRegistry("https://${HARBOR_REGISTRY}", 'harbor-credentials-id') {
                        def image = docker.build("${HARBOR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}")
                        image.push()
                        image.push('latest')
                    }
                }
            }
        }

        stage('Deploy') {
            steps {
                script {
                    withKubeConfig([credentialsId: 'k8s-config-id']) {
                        sh 'kubectl apply -k k8s/overlays/prod'
                        sh "kubectl set image deployment/code-sandbox-deployment code-sandbox=${HARBOR_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG} -n ${K8S_NAMESPACE}"
                        sh "kubectl annotate deployment/code-sandbox-deployment kubernetes.io/change-cause='Jenkins build ${BUILD_NUMBER}' --overwrite -n ${K8S_NAMESPACE}"
                        sh "kubectl rollout status deployment/code-sandbox-deployment --timeout=180s -n ${K8S_NAMESPACE}"
                    }
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
