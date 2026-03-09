# Maestro MCP — Developer Guide

Maestro is a multi-host machine fleet orchestration layer and AI agent orchestra, exposed via the Model Context Protocol (MCP). It turns a collection of SSH-accessible machines into a unified workspace.

## Architecture

Maestro is a modular Python package with a slim entry point:

- **Entry Point (`server.py`):** Configures FastMCP, sets up OAuth, wires modules, and starts the server (stdio or streamable-http).
- **Core Package (`maestro/`):**
    - **`tools/fleet.py`:** Core fleet operations: `exec`, `script`, `read`, `write`, `transfer`, `status`.
    - **`tools/orchestra.py`:** Agent dispatch (`codex`, `gemini`, `claude`), task registry, and auto-promote logic.
    - **`hosts.py`:** Fleet topology management and `hosts.yaml` parsing. Supports Bash and PowerShell.
    - **`transport.py`:** Persistent SSH ControlMaster lifecycle (warmup, teardown, transient failure retries).
    - **`local.py`:** Zero-overhead execution for the "hub" (is_local: true) machine.
    - **`relay.py`:** HTTP endpoints for high-speed file transfers bypassing the LLM context.
    - **`oauth_state.py`:** Atomic JSON persistence for OAuth clients and tokens (survives restarts).
    - **`config.py`:** Environment-based configuration (MaestroConfig).

## Key Patterns

### 1. Auto-Promote (block_timeout)
Execution tools use `_auto_promote()` to handle long-running tasks:
- **Inline:** Try to finish within `block_timeout` (client-dependent: 30s local, 0s remote).
- **Background:** If timeout exceeds, tasks are shielded and moved to `TASK_REGISTRY`.
- **Polling:** Returns a `task_id`. Use `poll(task_id)` to get the final result.

### 2. State Persistence
OAuth state (clients, access/refresh tokens) is persisted to `~/.maestro/oauth_state.json`. This ensures that active sessions and registered clients are **not** lost when the Maestro service restarts.

### 3. Context Budget Awareness
Tool responses consume LLM context tokens.
- **Surgical Reads:** Use `read` with `head` or `tail` parameters.
- **Large Files:** Use `transfer` to move files to the hub machine; the response is just an `[OK]`.
- **Orchestra Output:** Agent output is saved to disk; only a preview is returned. Use `read_output` for targeted inspection.

## Engineering Standards

- **Error Handling:** Distinguish between transient SSH failures (retried) and permanent errors (reported).
- **Security:** Never log or commit secrets. Use the PIN gate (`MAESTRO_AUTHORIZE_PIN_HASH`) for remote access.
- **Cross-Platform:** Always check `config.shell` before generating commands. Use `_ps_quote` for PowerShell.
- **Atomicity:** `OAuthStateStore` uses a write-to-tmp-and-replace strategy to prevent state corruption.

## Development

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python server.py --transport stdio

# Test
pytest tests/
```

## Critical Rules

1. **Don't kill the Maestro process** via Maestro tools; it terminates the connection.
2. **hosts.yaml is gitignored.** Use `hosts.example.yaml` for fleet definition.
3. **Docs & Logs:** `docs/` is gitignored. Use `journal/` for persistent session records.
4. **Environment:** `MAESTRO_ISSUER_URL` is required for HTTP transport.
