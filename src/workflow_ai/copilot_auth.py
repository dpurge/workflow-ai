"""GitHub Copilot auth helpers: config read/write, device-flow login, status.

This module is Pi-independent. It manages workflow-ai's own credential file at
~/.config/workflow-ai/copilot.json.

Security rules:
- Never log or include token values (ghu_..., tid=...) in error messages.
- Only user_code is printed to the user during login.
- Config written 0600; parent dir 0700; atomic write via temp file + os.replace.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .backends.base import AgentOutputError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "workflow-ai" / "copilot.json"

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
_SESSION_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"

_KNOWN_MODELS = [
    "claude-opus-4.6",
    "claude-sonnet-4.6",
    "gemini-3.1-pro-preview",
    "gpt-5.3-codex",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5-mini",
    "claude-sonnet-4.5",
    "claude-opus-4.5",
    "claude-haiku-4.5",
    "gemini-3-flash-preview",
]

# ---------------------------------------------------------------------------
# SSL context — use system trust store so corporate CA bundles are honoured.
# REQUESTS_CA_BUNDLE / SSL_CERT_FILE env vars provide an explicit override.
# ---------------------------------------------------------------------------


def _ssl_verify() -> ssl.SSLContext | str | bool:
    """Return an SSL verification argument suitable for httpx.

    Precedence:
    1. REQUESTS_CA_BUNDLE env var (path to CA bundle file)
    2. SSL_CERT_FILE env var (path to CA bundle file)
    3. System default SSL context (uses macOS/Linux trust store incl. corporate CAs)
    """
    for env_var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        ca_bundle = os.environ.get(env_var)
        if ca_bundle:
            return ca_bundle
    return ssl.create_default_context()


# ---------------------------------------------------------------------------
# Config path resolution
# ---------------------------------------------------------------------------


def resolve_config_path(override: str | None = None) -> Path:
    """Resolve config path: arg > WORKFLOW_AI_COPILOT_CONFIG env > default."""
    if override is not None:
        return Path(override)
    env = os.environ.get("WORKFLOW_AI_COPILOT_CONFIG")
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# Config read/write
# ---------------------------------------------------------------------------


def read_creds(path: Path) -> dict:
    """Load and return the github-copilot block from the config file.

    Returns a dict with at least 'refresh', 'access', 'expires' keys.
    Raises AgentOutputError on missing file, invalid JSON, or missing fields.
    """
    if not path.exists():
        raise AgentOutputError(
            f"CopilotBackend: cannot read config {path}: file not found"
        )
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AgentOutputError(
            f"CopilotBackend: cannot read config {path}: {exc}"
        ) from exc

    if not isinstance(raw, dict) or "github-copilot" not in raw:
        raise AgentOutputError(
            f"CopilotBackend: 'github-copilot' missing in {path}"
        )

    block: dict = raw["github-copilot"]
    for field in ("refresh", "access", "expires"):
        if field not in block:
            raise AgentOutputError(
                f"CopilotBackend: '{field}' missing in {path}['github-copilot']"
            )
    return block


def write_creds(
    path: Path, *, refresh: str, access: str, expires_ms: int
) -> None:
    """Atomically write credentials to the config file (0600, parent dir 0700).

    Creates the temp file in the same directory as the target so that
    os.replace is an intra-filesystem rename (N2 reviewer concern).
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    payload = {
        "github-copilot": {
            "type": "oauth",
            "refresh": refresh,
            "access": access,
            "expires": expires_ms,
        }
    }
    content = json.dumps(payload, indent=2).encode("utf-8")

    # Temp file in same directory for intra-filesystem rename
    tmp_path = path.with_suffix(".json.tmp")
    try:
        tmp_path.write_bytes(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Device flow helpers
# ---------------------------------------------------------------------------


def _device_code(client_id: str) -> dict:
    """POST to GitHub device code endpoint, return the response dict."""
    resp = httpx.post(
        _DEVICE_CODE_URL,
        json={"client_id": client_id, "scope": "copilot"},
        headers={"Accept": "application/json"},
        timeout=30,
        verify=_ssl_verify(),
    )
    if resp.status_code != 200:
        raise AgentOutputError(
            f"copilot login: device code request failed: HTTP {resp.status_code}"
        )
    data = resp.json()
    return data


def _poll_token(
    client_id: str,
    device_code: str,
    interval: int,
    expires_in: int,
    timeout_s: int,
) -> str:
    """Poll the OAuth token endpoint until success or abort condition.

    Honors authorization_pending (keep polling), slow_down (increase interval),
    expired_token / access_denied / overall timeout (abort).

    Per N1 reviewer concern: checks both expires_in (device-code lifetime) AND
    timeout_s on every iteration. Also aborts on expired_token and access_denied.

    Returns the GitHub OAuth access_token (ghu_...).
    """
    start = time.time()
    poll_interval = interval

    while True:
        elapsed = time.time() - start
        # N1: check both device-code lifetime and overall timeout_s per iteration
        if elapsed >= expires_in or elapsed >= timeout_s:
            raise AgentOutputError(
                "copilot login: authorization timed out — run login again"
            )

        time.sleep(poll_interval)

        resp = httpx.post(
            _OAUTH_TOKEN_URL,
            json={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=30,
            verify=_ssl_verify(),
        )
        if resp.status_code != 200:
            raise AgentOutputError(
                f"copilot login: OAuth poll failed: HTTP {resp.status_code}"
            )

        data = resp.json()

        if "error" in data:
            error_code = data["error"]
            if error_code == "authorization_pending":
                continue
            elif error_code == "slow_down":
                # N1: server hints slow_down — increase interval
                server_interval = data.get("interval")
                if server_interval:
                    poll_interval = server_interval
                else:
                    poll_interval += 5
                continue
            elif error_code == "expired_token":
                # N1: explicit abort on expired_token
                raise AgentOutputError(
                    "copilot login: authorization timed out — run login again"
                )
            elif error_code == "access_denied":
                # N1: explicit abort on access_denied
                raise AgentOutputError(
                    "copilot login: authorization denied by user"
                )
            else:
                raise AgentOutputError(
                    f"copilot login: OAuth error: {error_code}"
                )

        if "access_token" in data:
            return data["access_token"]

        raise AgentOutputError("copilot login: unexpected OAuth response")


def _exchange_session_token(github_token: str) -> tuple[str, int]:
    """Exchange GitHub OAuth token for a Copilot session token.

    Returns (session_token, expires_ms).
    Never logs or includes github_token in error messages.
    """
    resp = httpx.get(
        _SESSION_TOKEN_URL,
        headers={
            "Authorization": f"token {github_token}",
            "user-agent": "GithubCopilot/1.155.0",
        },
        timeout=30,
        verify=_ssl_verify(),
    )
    if resp.status_code != 200:
        raise AgentOutputError(
            f"copilot login: token exchange failed: HTTP {resp.status_code}"
        )
    data = resp.json()
    if "token" not in data:
        raise AgentOutputError(
            "copilot login: token exchange failed: 'token' not in response"
        )
    # expires_at is Unix seconds; normalize to ms
    expires_ms = int(data["expires_at"]) * 1000
    return data["token"], expires_ms


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------


def login(
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    config_path: str | None = None,
    timeout_s: int = 900,
) -> None:
    """Run the GitHub device-flow login sequence and persist credentials.

    Prints user_code + verification_uri only — never device_code or tokens.
    """
    path = resolve_config_path(config_path)

    # Step 1: request device code
    dc_data = _device_code(client_id)
    device_code_val = dc_data["device_code"]
    user_code = dc_data["user_code"]
    verification_uri = dc_data["verification_uri"]
    interval = int(dc_data.get("interval", 5))
    expires_in = int(dc_data.get("expires_in", 900))

    # Step 2: show only user_code and verification_uri
    print(f"\nOpen {verification_uri} and enter code: {user_code}\n", flush=True)

    # Step 3: poll for OAuth token
    github_token = _poll_token(
        client_id=client_id,
        device_code=device_code_val,
        interval=interval,
        expires_in=expires_in,
        timeout_s=timeout_s,
    )

    # Step 4: exchange for session token
    session_token, expires_ms = _exchange_session_token(github_token)

    # Step 5: persist credentials
    write_creds(
        path,
        refresh=github_token,
        access=session_token,
        expires_ms=expires_ms,
    )

    # Step 6: print confirmation (path + expiry)
    from datetime import datetime, timezone

    expiry_local = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc).astimezone()
    print(
        f"Logged in. Credentials written to {path}\n"
        f"Session token expires: {expiry_local.strftime('%Y-%m-%d %H:%M %Z')}",
        flush=True,
    )


def status(*, config_path: str | None = None) -> None:
    """Print the current Copilot credential status."""
    path = resolve_config_path(config_path)

    if not path.exists():
        print("not logged in — run 'workflow-ai copilot login'")
        return

    try:
        block = read_creds(path)
    except AgentOutputError as exc:
        print(str(exc))
        return

    expires_ms = block["expires"]
    now_ms = time.time() * 1000

    if now_ms >= expires_ms:
        print("token expired — run 'workflow-ai copilot login'")
        return

    from datetime import datetime, timezone

    expiry_local = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc).astimezone()
    models = block.get("availableModelIds", _KNOWN_MODELS)
    print(f"Logged in. Token expires: {expiry_local.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Config: {path}")
    print("Available models:")
    for m in models:
        print(f"  {m}")
