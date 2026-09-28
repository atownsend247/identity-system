// Build, test, and deploy identity-system to a Proxmox LXC container.
//
// Assumes a Multibranch Pipeline job (so env.BRANCH_NAME is populated by
// the `when { branch 'main' }` guard below). Modelled on the finance-system
// Jenkinsfile this was originally copied from: Node isn't provisioned via
// a Jenkins-managed "NodeJS Tool Installation" - the environment block
// below puts `nodenv`'s shims on PATH directly, and version dispatch comes
// from `frontend/.node-version` (22.17.0), same as local dev. The agent
// needs that nodenv version already installed (`nodenv install 22.17.0`).
// uv is bootstrapped inline (Install uv stage) rather than assumed
// pre-installed on the agent, and `uv sync --locked` against the committed
// uv.lock is what pins the backend toolchain (including Python itself, per
// .python-version/pyproject.toml's requires-python - uv downloads 3.13
// itself if the agent doesn't already have it). The Deploy stage rsyncs
// this checkout (including the frontend/dist/ the Frontend stage just
// built) to DEPLOY_HOST and runs deploy/remote-setup.sh there over SSH
// (see deploy/deploy.sh) - this assumes the Jenkins agent already has
// working SSH key-based access to the container (no Jenkins-managed
// credential/plugin involved). See deploy/remote-setup.sh and
// deploy/identity-system-api.service for the one-time container setup
// this pipeline assumes (service user, __BACKEND_DIR__/.env, and what
// each DEPLOY_* variable below means) - note remote-setup.sh itself still
// installs the backend via plain pip/venv, not uv, so `uv.lock` only pins
// what CI tests against, not what's deployed.

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        PATH = "${env.HOME}/.nodenv/bin:${env.HOME}/.nodenv/shims:${env.HOME}/.local/bin:${env.PATH}"

        // -- Proxmox LXC deploy target - fill in for your environment,
        // see deploy/remote-setup.sh --
        DEPLOY_HOST        = '192.168.71.29'
        DEPLOY_USER        = 'root'
        BACKEND_DIR        = '/opt/identity-system'
        BACKEND_SERVICE    = 'identity-system-api'
    }

    stages {
        stage('Prereq') {
            steps {
                sh 'eval "$(nodenv init -)" && nodenv versions'
            }
        }

        stage('Install uv') {
            steps {
                sh 'curl -LsSf https://astral.sh/uv/install.sh | sh'
            }
        }

        stage('Checkout') {
            steps {
                script {
                    def scmVars = checkout scm
                    echo "Branch: ${scmVars.GIT_BRANCH} (env.BRANCH_NAME: ${env.BRANCH_NAME})"
                }
            }
        }

        stage('Test') {
            parallel {
                stage('Backend') {
                    steps {
                        // --locked fails loudly if pyproject.toml and
                        // uv.lock have drifted apart, instead of silently
                        // re-resolving. Unlike the uv-native sibling
                        // projects, `dev` here is a plain
                        // [project.optional-dependencies] extra (kept that
                        // way so README.md's `pip install -e ".[dev]"`
                        // still works for anyone not using uv) rather than
                        // a [dependency-groups] group - so, unlike a bare
                        // `uv sync --locked`, this needs --extra dev
                        // spelled out to actually pull in pytest/httpx.
                        sh '''
                            mkdir -p reports
                            uv sync --locked --extra dev
                            uv run pytest --junitxml=reports/junit-backend.xml
                        '''
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'reports/junit-backend.xml'
                        }
                    }
                }

                stage('Frontend') {
                    steps {
                        dir('frontend') {
                            sh '''
                                mkdir -p ../reports
                                npm ci
                                npm run lint
                                npm test -- --reporter=junit --outputFile=../reports/junit-frontend.xml
                                npm run build
                            '''
                        }
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'reports/junit-frontend.xml'
                        }
                    }
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
