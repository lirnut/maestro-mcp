# AGENTS.md - Maestro MCP Development Guide

This document provides essential information for AI coding agents working on the Maestro MCP codebase.

## Project Overview

Maestro is a multi-host machine fleet orchestration layer and AI agent orchestra, exposed via the Model Context Protocol (MCP). It turns SSH-accessible machines into a unified workspace.

## Build/Lint/Test Commands

### Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"  # Installs pytest, pytest-asyncio
```

### Run Server
```bash
# stdio transport (for Claude Code, Codex CLI, Claude Desktop)
python server.py --transport stdio

# HTTP transport (for remote access via tunnel)
python server.py --transport streamable-http --port 8222 --host 127.0.0.1
```

### Run Tests
```bash
# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_primitives.py

# Run a single test class
pytest tests/test_primitives.py::TestPsQuote

# Run a single test
pytest tests/test_primitives.py::TestPsQuote::test_simple_path -v
```

### Syntax Check
```bash
python -m py_compile maestro/tools/fleet.py
```

## Code Style Guidelines

### Imports
```python
# Standard library first (alphabetically sorted)
from __future__ import annotations
import asyncio
import json
import logging
import shlex
from datetime import datetime, timezone
from pathlib import Path

# Third-party imports
import yaml

# Local imports (grouped by module)
from maestro.config import MaestroConfig
from maestro.hosts import HOSTS, HostConfig, HostShell
```

### Type Annotations
- Use `from __future__ import annotations` at the top of all files
- Use modern union syntax: `str | None` instead of `Optional[str]`
- Use `list[str]` instead of `List[str]`
- Use `dict[str, Any]` instead of `Dict[str, Any]`

```python
# Good
def get_session(session_id: str) -> SessionInfo | None:
    sessions: list[SessionInfo] = []
    data: dict[str, Any] = {}

# Bad
from typing import Optional, List, Dict
def get_session(session_id: str) -> Optional[SessionInfo]:
    sessions: List[SessionInfo] = []
```

### Dataclasses
- Use `@dataclass` for configuration and state objects
- Use `frozen=True` for immutable configs

```python
@dataclass(frozen=True)
class MaestroConfig:
    issuer_url: str
    ssh_timeout: int

@dataclass
class SessionInfo:
    session_id: str
    status: str
    created_at: str
```

### Enums
```python
from enum import Enum

class HostStatus(Enum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"

class HostShell(Enum):
    BASH = "bash"
    POWERSHELL = "powershell"

class RemoteCLI(Enum):
    OPENCODE = "opencode"
    CODEX = "codex"
    GEMINI = "gemini"
    CLAUDE = "claude"
```

### Error Handling
- Distinguish transient failures (retry) from permanent errors (report)
- Use specific error messages with context

```python
# Transient SSH failure (retry)
if _is_transient_failure(rc, stderr):
    await pool.get_connection(host_name, params)
    return await pool.run_command(host_name, params, command, timeout)

# Permanent error (raise)
raise RuntimeError(f"Failed to save session {session_id}: {e}")
```

### File I/O
- Use `pathlib.Path` for all path operations
- Use atomic write pattern (write to temp, then rename)

```python
# Atomic write
temp_file = target_file.with_suffix(".tmp")
temp_file.write_text(content, encoding="utf-8")
temp_file.rename(target_file)
```

### Shell Command Building
- Use `shlex.quote()` for Bash escaping
- Use `_ps_quote()` for PowerShell escaping
- Always check `config.shell` before generating commands

```python
import shlex

# Bash
cmd = f"ls {shlex.quote(path)}"

# PowerShell
from maestro.hosts import _ps_quote
cmd = f"Get-ChildItem {_ps_quote(path)}"
```

### MCP Tool Definitions
```python
@mcp.tool()
async def my_tool(host: str, param: str = "default") -> str:
    """Brief description of what the tool does.

    Use this for: Specific use case description.

    Args:
        host: Target host name from fleet (call status() to see available hosts)
        param: Description of parameter

    Returns:
        JSON string with result
    """
    # Implementation
    return json.dumps({"status": "success"})
```

### Async Patterns
- Use `asyncio` for all async operations
- Use `asyncio.create_task()` for background tasks
- Use `asyncio.Lock` for shared state

```python
async def process_with_timeout(session_id: str, timeout: int) -> str:
    try:
        result = await asyncio.wait_for(
            _execute(session_id),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        return json.dumps({"status": "timeout"})
```

## Naming Conventions

- **Modules**: `lowercase_with_underscores.py`
- **Classes**: `PascalCase`
- **Functions/Methods**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private functions**: Prefix with `_` (e.g., `_load_hosts`)
- **Module-level state**: `UPPER_SNAKE_CASE` (e.g., `HOSTS`, `TASK_REGISTRY`)

## Project Structure

```
maestro-mcp/
├── server.py              # Entry point, FastMCP setup
├── maestro/
│   ├── config.py          # MaestroConfig dataclass
│   ├── hosts.py           # Fleet topology, HostConfig, SSH config parsing
│   ├── ssh_pool.py        # SSH connection pool using asyncssh
│   ├── transport.py       # SSH transport layer
│   ├── local.py           # Zero-overhead local execution
│   ├── session_manager.py # Persistent session management
│   ├── tools/
│   │   ├── fleet.py       # Core tools + orchestra + persistent sessions
│   │   └── orchestra.py   # Agent dispatch, auto-promote, task registry
│   └── oauth_state.py     # OAuth state persistence
├── tests/
│   ├── test_primitives.py # Unit tests for pure functions
│   └── test_oauth.py      # OAuth tests
├── hosts.yaml             # Fleet definition (gitignored - contains secrets)
├── hosts.example.yaml     # Example fleet config
└── .env                   # Secrets (gitignored)
```

## Critical Rules

1. **Don't kill the Maestro process** via Maestro tools
2. **Never edit hosts.yaml** - This file contains sensitive info (passwords, IPs). Users configure it manually, not agents.
3. **hosts.yaml is gitignored** - never commit it, use `hosts.example.yaml` for examples
4. **Never commit secrets** - passwords, IP addresses, host names are sensitive
5. **Cross-platform awareness** - Always check `config.shell` before commands
6. **Context budget** - Use `head`/`tail` parameters, avoid large outputs
7. **No sensitive info in examples** - Use generic names like "my-server" not real host names
8. **Get hosts from status()** - Call status() to see available hosts, don't hardcode or assume host names

## SSH Connection Architecture

Maestro uses `asyncssh` library for SSH connections:

- **Connection Pool**: `ssh_pool.py` manages connections with automatic keepalive
- **Authentication Priority**: SSH Agent → Key + passphrase → Password
- **Config Loading**: SSH params parsed from `~/.ssh/config` automatically
- **Host Config**: Password can be set in `hosts.yaml` for auto-auth

```python
# Connection params from HostConfig
params = SSHConnectionParams(
    host=cfg.hostname or cfg.alias,
    port=cfg.port or 22,
    user=cfg.user or "",
    password=cfg.password or "",
    key_path=cfg.key_path or "",
    key_passphrase=cfg.key_passphrase or "",
)
```

## Key Patterns

### Auto-Promote
Long-running tasks automatically become background tasks:
```python
return await _auto_promote(
    _execute,
    block_timeout=block_timeout,
    agent="opencode",
    host=host,
    prompt=prompt,
)
```

### PATH Fix for SSH
SSH non-interactive sessions don't load user PATH. Always prefix:
```python
_PATH_FIX = "export PATH=$PATH:~/.local/bin:~/bin:~/.opencode/bin 2>/dev/null; "
```

### Session Persistence
Use `nohup` + PID file for hosts without tmux:
```python
nohup bash -c '{cli_cmd}' > {output_file} 2>&1 &
echo $! > {pid_file} && disown
```

## Tool Documentation Best Practices

When adding new tools, follow this pattern:

1. **"Use this for"** - Explain when to use the tool
2. **Args section** - Document each parameter
3. **Returns section** - Describe output format
4. **Generic examples** - Use "my-host" not real host names

```python
@mcp.tool()
async def my_tool(host: str, command: str) -> str:
    """Run a command on a remote host.

    Use this for: Single shell commands like docker ps, git status.

    Args:
        host: Host name from fleet (call status() to see available hosts)
        command: Shell command to execute

    Returns:
        Command output or error message
    """
```