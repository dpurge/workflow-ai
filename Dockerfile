FROM node:22-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# System deps: git (workflows may clone repos), ssh client, curl (uv installer), ca-certs.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Node-based agent backends -------------------------------------------
# claude: native Bun binary downloaded by the npm package for the target platform.
# pi, codex: Node.js scripts.
RUN npm install -g --no-audit --no-fund \
        @anthropic-ai/claude-code@2.1.199 \
        @earendil-works/pi-coding-agent@0.80.2 \
        @openai/codex@0.140.0 \
    && npm cache clean --force

# --- uv + Python + workflow-ai -------------------------------------------
ENV UV_HOME=/opt/uv
ENV PATH="${UV_HOME}/bin:${PATH}"
RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="${UV_HOME}/bin" sh

WORKDIR /app

# Copy dependency manifest first for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
    && uv cache clean

# Copy source; skills directories inside the package are included here.
COPY src/ src/

# --- Runtime defaults ----------------------------------------------------
# Outputs land in /runs; mount it as a volume to retrieve results from CI.
ENV WORKFLOW_OUT=/runs

WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
