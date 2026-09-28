// Build, test, and deploy identity-system to a Proxmox LXC container.
//
// Assumes a Multibranch Pipeline job (so env.BRANCH_NAME is populated by
// the `when { branch 'main' }` guard below). This is a plain FastAPI
// backend with no frontend build (see CLAUDE.md) - Test just builds a
// throwaway venv and installs the package the same way README.md's local
// dev instructions do (`pip install -e ".[dev]"`; there's no uv.lock here
// to make `uv sync` meaningful), after checking the agent's system python
// is actually 3.13 (see pyproject.toml/.python-version) rather than
// silently testing against whatever's installed. The Deploy stage rsyncs
// this checkout to
// DEPLOY_HOST and runs deploy/remote-setup.sh there over SSH (see
// deploy/deploy.sh) - this assumes the Jenkins agent already has working
// SSH key-based access to the container (no Jenkins-managed credential/
// plugin involved). See deploy/remote-setup.sh and
// deploy/identity-system-api.service for the one-time container setup
// this pipeline assumes (service user, __BACKEND_DIR__/.env, and what
// each DEPLOY_* variable below means).

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        // -- Proxmox LXC deploy target - fill in for your environment,
        // see deploy/remote-setup.sh --
        DEPLOY_HOST        = '192.168.71.29'
        DEPLOY_USER        = 'root'
        BACKEND_DIR        = '/opt/identity-system'
        BACKEND_SERVICE    = 'identity-system-api'
    }

    stages {
        stage('Checkout') {
            steps {
                script {
                    def scmVars = checkout scm
                    echo "Branch: ${scmVars.GIT_BRANCH} (env.BRANCH_NAME: ${env.BRANCH_NAME})"
                }
            }
        }

        stage('Test') {
            steps {
                // Fails loudly rather than silently testing against the
                // wrong interpreter if this agent's system python isn't
                // 3.13 (see pyproject.toml's requires-python /
                // .python-version, and deploy/remote-setup.sh's matching
                // check for the production side of this same assumption).
                sh '''
                    python_version="$(python -c 'import platform; print(platform.python_version())')"
                    case "$python_version" in
                        3.13.*) ;;
                        *) echo "Jenkins agent python is $python_version, not 3.13.x" >&2; exit 1 ;;
                    esac

                    python -m venv .venv
                    .venv/bin/pip install --no-cache-dir -e ".[dev]"
                    mkdir -p reports
                    .venv/bin/pytest --junitxml=reports/junit.xml
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Deploy') {
            when {
                branch 'main'
            }
            steps {
                sh 'chmod +x deploy/deploy.sh && ./deploy/deploy.sh'
            }
        }
    }

    post {
        always {
            cleanWs()
        }
    }
}
