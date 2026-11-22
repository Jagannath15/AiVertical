// Phase 1 stub — validates the Jenkins + GitHub pipeline connection.
// Phase 2 will replace the Placeholder stage with real scanner stages.

pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        skipDefaultCheckout()
        // Keep only the last 20 builds to save disk space
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Placeholder') {
            steps {
                echo "=== AI Vertical CI — Phase 1 stub ==="
                echo "Branch  : ${env.BRANCH_NAME ?: env.GIT_BRANCH ?: 'unknown'}"
                echo "Commit  : ${env.GIT_COMMIT ?: 'unknown'}"
                echo "Build # : ${env.BUILD_NUMBER}"
                echo "Scanners and AI review will run here in Phase 2."
            }
        }
    }

    post {
        always {
            echo "Pipeline finished. Result: ${currentBuild.currentResult}"
        }
        success {
            echo "All checks passed — ready for Phase 2 scanner stages."
        }
        failure {
            echo "Pipeline failed. Check the stage logs above."
        }
    }
}
