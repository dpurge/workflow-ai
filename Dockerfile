# syntax=docker/dockerfile:1
#
# Small Python + uv image for workflow-ai. The agent backends are pure-Python
# SDKs (anthropic / openai / GitHub Copilot via httpx), so there is NO Node
# runtime — the base image ships uv + Python 3.12 and nothing else heavy.
#
# PEP 723 script tools (RAG, web/wiki search) resolve their inline dependencies
# at runtime via `uv run --script`, so they are not baked into the image
# (keeps it small; first RAG use needs network to fetch the embedding deps).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# System deps: git (workflows may clone repos), ssh client (SSH auth in the
# entrypoint), ca-certificates.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build metadata (passed by CI as build-args).
ARG VERSION=dev
ARG COMMIT=unknown
ARG BRANCH=unknown
LABEL org.opencontainers.image.title="workflow-ai" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}" \
      org.opencontainers.image.ref.name="${BRANCH}"

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 1) Install dependencies first (cache layer) from the manifests only.
#    README.md is copied too because pyproject declares it as the project readme
#    (hatchling reads it when the project itself is installed in step 2).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Copy source (bundled skills under src/workflow_ai/ebook/skills are included)
#    and install the project itself into the same venv.
COPY src/ src/
RUN uv sync --frozen --no-dev \
    && uv cache clean

# Runtime defaults; outputs land in /runs (mount it to retrieve results).
ENV WORKFLOW_OUT=/runs
WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
