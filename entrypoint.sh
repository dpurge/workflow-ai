#!/bin/sh
set -eu

# --- SSH setup -----------------------------------------------------------
# Inject a PEM private key so workflows can clone/push over SSH.
if [ -n "${SSH_PRIVATE_KEY:-}" ]; then
    mkdir -p "${HOME}/.ssh"
    chmod 700 "${HOME}/.ssh"

    printf '%s\n' "${SSH_PRIVATE_KEY}" > "${HOME}/.ssh/id_rsa"
    chmod 600 "${HOME}/.ssh/id_rsa"

    if [ -n "${SSH_KNOWN_HOSTS:-}" ]; then
        printf '%s\n' "${SSH_KNOWN_HOSTS}" > "${HOME}/.ssh/known_hosts"
    else
        # Auto-populate github.com host key so workflows can reach GitHub
        # without a manual trust prompt.
        ssh-keyscan -H github.com >> "${HOME}/.ssh/known_hosts" 2>/dev/null
    fi
    chmod 644 "${HOME}/.ssh/known_hosts"
fi

# --- Validate required inputs --------------------------------------------
if [ -z "${WORKFLOW:-}" ]; then
    echo "ERROR: WORKFLOW environment variable is required." >&2
    echo "  Example: WORKFLOW=research" >&2
    exit 1
fi

# --- Build workflow-ai CLI arguments -------------------------------------
set -- "${WORKFLOW}"

[ -n "${WORKFLOW_PROMPT:-}"           ] && set -- "$@" --prompt           "${WORKFLOW_PROMPT}"
[ -n "${WORKFLOW_BACKEND:-}"          ] && set -- "$@" --backend          "${WORKFLOW_BACKEND}"
[ -n "${WORKFLOW_MODEL:-}"            ] && set -- "$@" --model            "${WORKFLOW_MODEL}"
[ -n "${WORKFLOW_API_BASE_URL:-}"     ] && set -- "$@" --api-base-url     "${WORKFLOW_API_BASE_URL}"
[ -n "${WORKFLOW_API_KEY:-}"          ] && set -- "$@" --api-key          "${WORKFLOW_API_KEY}"
[ -n "${WORKFLOW_OUT:-}"              ] && set -- "$@" --out              "${WORKFLOW_OUT}"

# ebook-specific parameters
[ -n "${WORKFLOW_SOURCE:-}"           ] && set -- "$@" --source           "${WORKFLOW_SOURCE}"
[ -n "${WORKFLOW_LEVEL:-}"            ] && set -- "$@" --level            "${WORKFLOW_LEVEL}"
[ -n "${WORKFLOW_TRANSLATION_LANG:-}" ] && set -- "$@" --translation-lang "${WORKFLOW_TRANSLATION_LANG}"
[ -n "${WORKFLOW_CWD:-}"              ] && set -- "$@" --cwd              "${WORKFLOW_CWD}"
[ -n "${WORKFLOW_EBOOK_YML:-}"        ] && set -- "$@" --ebook-yml        "${WORKFLOW_EBOOK_YML}"
[ -n "${WORKFLOW_KIND:-}"             ] && set -- "$@" --kind             "${WORKFLOW_KIND}"
[ -n "${WORKFLOW_LANG:-}"             ] && set -- "$@" --lang             "${WORKFLOW_LANG}"
[ -n "${WORKFLOW_SCRIPT:-}"           ] && set -- "$@" --script           "${WORKFLOW_SCRIPT}"
[ -n "${WORKFLOW_CHAPTER:-}"          ] && set -- "$@" --chapter          "${WORKFLOW_CHAPTER}"
[ -n "${WORKFLOW_FORM:-}"             ] && set -- "$@" --form             "${WORKFLOW_FORM}"
[ -n "${WORKFLOW_SEARCHES:-}"         ] && set -- "$@" --searches          "${WORKFLOW_SEARCHES}"

# research-specific parameters
[ -n "${WORKFLOW_DEPTH:-}"            ] && set -- "$@" --depth            "${WORKFLOW_DEPTH}"

# Verbose is always on: CI consumers read structured progress from stdout.
set -- "$@" --verbose

# --- Run -----------------------------------------------------------------
exec uv run --project /app workflow-ai run "$@"
