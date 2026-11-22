// Phase 2 — scanners, PR comment, status check.
// Secret found => abort immediately, before any LLM call (none exist yet; AI review is Phase 3).

pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        skipDefaultCheckout()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    stages {
        stage('Checkout') {
            steps {
                script {
                    def scmVars = checkout(scm)
                    env.GIT_COMMIT = scmVars.GIT_COMMIT
                    env.GIT_BRANCH = scmVars.GIT_BRANCH
                    def remote = sh(script: 'git remote get-url origin', returnStdout: true).trim()
                    env.GH_REPO = remote.replaceAll('.*github\\.com[:/]', '').replaceAll('\\.git$', '')
                }
            }
        }

        stage('Detect PR') {
            steps {
                withCredentials([string(credentialsId: 'github-token', variable: 'GITHUB_TOKEN')]) {
                    script {
                        def branch = (env.GIT_BRANCH ?: env.BRANCH_NAME ?: 'HEAD').replaceFirst('^origin/', '')
                        if (branch in ['main', 'master']) {
                            currentBuild.result = 'NOT_BUILT'
                            echo "Push to ${branch} — no PR to check. Skipping scanner stages."
                            return
                        }
                        def pr
                        withEnv(["PR_BRANCH=${branch}"]) {
                            pr = sh(
                                script: 'python3 -m scripts.github_api get-pr "$GH_REPO" "$PR_BRANCH"',
                                returnStdout: true
                            ).trim()
                        }
                        if (!pr || pr == 'none') {
                            currentBuild.result = 'NOT_BUILT'
                            echo "No open PR for branch ${branch}. Skipping scanner stages."
                            return
                        }
                        env.PR_NUMBER = pr
                        echo "Detected PR #${pr} for branch ${branch}"
                        sh "python3 -m scripts.github_api set-status '${env.GH_REPO}' '${env.GIT_COMMIT}' pending 'Scanning...' jenkins/pr-check"
                    }
                }
            }
        }

        stage('Extract Merge Base') {
            when { expression { env.PR_NUMBER != null } }
            steps {
                sh 'git fetch --no-tags origin main || git fetch --no-tags origin master || true'
                script {
                    env.MERGE_BASE = sh(
                        script: 'git merge-base origin/main HEAD 2>/dev/null || git merge-base origin/master HEAD 2>/dev/null || git rev-parse HEAD^ 2>/dev/null || git rev-parse HEAD',
                        returnStdout: true
                    ).trim()
                }
                sh 'mkdir -p findings'
            }
        }

        stage('Scan Secrets') {
            when { expression { env.PR_NUMBER != null } }
            steps {
                script {
                    def rc = sh(script: 'bash scripts/scan_secrets.sh', returnStatus: true)
                    if (rc != 0) {
                        withCredentials([string(credentialsId: 'github-token', variable: 'GITHUB_TOKEN')]) {
                            sh 'python3 -m scripts.post_comment'
                        }
                        error('Secret scan blocked the pipeline (secret found, or scan could not be verified) — aborted before any LLM call')
                    }
                }
            }
        }

        stage('Scan Code') {
            when { expression { env.PR_NUMBER != null } }
            steps { sh 'bash scripts/scan_code.sh' }
        }

        stage('Scan Deps') {
            when { expression { env.PR_NUMBER != null } }
            steps { sh 'bash scripts/scan_deps.sh' }
        }

        stage('Run Linters') {
            when { expression { env.PR_NUMBER != null } }
            steps { sh 'bash scripts/run_linters.sh' }
        }

        stage('Run Tests') {
            when { expression { env.PR_NUMBER != null } }
            steps {
                script {
                    def rc = sh(script: 'python3 -m pytest tests/ -v --tb=short > findings/test_output.txt 2>&1', returnStatus: true)
                    sh "python3 -m scripts.write_findings_json findings/tests.json pytest ${rc == 0 ? '1' : '0'} findings/test_output.txt"
                }
            }
        }

        stage('AI Review') {
            when { expression { env.PR_NUMBER != null } }
            steps {
                script {
                    try {
                        withCredentials([string(credentialsId: 'llm-api-key', variable: 'LLM_API_KEY')]) {
                            sh 'python3 -m scripts.ai_review'
                        }
                    } catch (Exception e) {
                        echo "AI review skipped: credential 'llm-api-key' not available (${e.getMessage()})"
                        sh '''python3 -c "import json; json.dump({'skipped': True, 'reason': 'llm-api-key credential not configured'}, open('findings/ai_review.json', 'w'))"'''
                    }
                }
            }
        }

        stage('Post Results') {
            when { expression { env.PR_NUMBER != null } }
            steps {
                withCredentials([string(credentialsId: 'github-token', variable: 'GITHUB_TOKEN')]) {
                    sh 'python3 -m scripts.post_comment'
                }
            }
        }
    }

    post {
        always {
            echo "Pipeline finished. Result: ${currentBuild.currentResult}"
        }
        unsuccessful {
            script {
                if (env.PR_NUMBER != null && env.GIT_COMMIT) {
                    withCredentials([string(credentialsId: 'github-token', variable: 'GITHUB_TOKEN')]) {
                        sh "python3 -m scripts.github_api set-status '${env.GH_REPO}' '${env.GIT_COMMIT}' error 'Pipeline failed unexpectedly' jenkins/pr-check || true"
                    }
                }
            }
        }
        cleanup {
            // Declarative's cleanup block always runs last, after `unsuccessful`,
            // so scripts/ is still present in the workspace when that stage needs it.
            deleteDir()
        }
    }
}
