FROM jenkins/jenkins:lts-jdk17

# Skip the interactive setup wizard; JCasC configures Jenkins on first boot
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
ENV CASC_JENKINS_CONFIG="/var/jenkins_casc"

# Install plugins as the jenkins user (image default)
COPY jenkins/plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

# ── System tools (root required) ──────────────────────────────────────────────
USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Gitleaks — bump GITLEAKS_VERSION to latest stable at https://github.com/gitleaks/gitleaks/releases
ARG GITLEAKS_VERSION=8.18.4
RUN set -eux; \
    curl -sSfL \
      "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
      -o /tmp/gitleaks.tar.gz; \
    tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks; \
    chmod +x /usr/local/bin/gitleaks; \
    rm /tmp/gitleaks.tar.gz

# Trivy — bump TRIVY_VERSION to latest stable at https://github.com/aquasecurity/trivy/releases
ARG TRIVY_VERSION=0.76.2
RUN set -eux; \
    curl -sSfL \
      "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
      -o /tmp/trivy.tar.gz; \
    tar -xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy; \
    chmod +x /usr/local/bin/trivy; \
    rm /tmp/trivy.tar.gz

# Semgrep — pin version for reproducible builds.
# Check https://pypi.org/project/semgrep/ for latest stable before upgrading.
ARG SEMGREP_VERSION=1.74.0
RUN pip3 install --no-cache-dir "semgrep==${SEMGREP_VERSION}" pytest --break-system-packages

# ── Back to unprivileged jenkins user ─────────────────────────────────────────
USER jenkins
