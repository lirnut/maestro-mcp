"""Client classification and per-client execution profiles."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request


# ---------------------------------------------------------------------------
# Per-client profiles — controls block_timeout and poll cooldown
# ---------------------------------------------------------------------------

CLIENT_PROFILES: dict[str, dict[str, Any]] = {
    "remote": {
        # Remote clients (Cloudflare tunnel / unknown) — constrained
        "block_timeout_agent": 0,     # always dispatch immediately
        "block_timeout_exec": 5,      # short inline window
        "poll_cooldown": 10,          # min seconds between polls
    },
    "local": {
        # Claude Code running on Apollyon — generous
        "block_timeout_agent": 30,    # try to complete inline
        "block_timeout_exec": 60,     # long inline for exec/script
        "poll_cooldown": 2,           # fast polls OK
    },
    "lan": {
        # LAN clients — middle ground
        "block_timeout_agent": 10,
        "block_timeout_exec": 20,
        "poll_cooldown": 5,
    },
}


@dataclass
class ClientContext:
    classification: str
    profile: dict[str, Any]
    client_id: str | None = None


_client_ctx: ContextVar[ClientContext] = ContextVar("_client_ctx")

# Default context for stdio / non-HTTP usage
_DEFAULT_CTX = ClientContext(
    classification="local",
    profile=CLIENT_PROFILES["local"],
)


def _classify_client(request: Request) -> str:
    """Classify a client from the HTTP request."""
    # Cloudflare Tunnel → remote
    if request.headers.get("cf-ray"):
        return "remote"

    # Check client IP
    client = request.client
    if client:
        host = client.host
        if host in ("127.0.0.1", "::1", "localhost"):
            return "local"
        if host.startswith("10.42.69."):
            return "lan"

    # Default: treat unknown as remote (safe)
    return "remote"


def set_client_context(request: Request) -> None:
    """Create ClientContext from request and set it in the contextvar."""
    classification = _classify_client(request)
    ctx = ClientContext(
        classification=classification,
        profile=CLIENT_PROFILES[classification],
    )
    _client_ctx.set(ctx)


def get_client_context() -> ClientContext:
    """Get the current client context, falling back to local defaults."""
    return _client_ctx.get(_DEFAULT_CTX)
