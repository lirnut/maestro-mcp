"""Host registry — fleet topology, status tracking, and command helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
import yaml
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from asyncssh.config import SSHClientConfig

    HAS_ASYNCSSH_CONFIG = True
except ImportError:
    HAS_ASYNCSSH_CONFIG = False


class HostStatus(Enum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class HostShell(Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


class RemoteCLI(Enum):
    OPENCODE = "opencode"
    CODEX = "codex"
    GEMINI = "gemini"
    CLAUDE = "claude"


@dataclass
class HostConfig:
    alias: str
    display_name: str
    description: str
    shell: HostShell = HostShell.BASH
    is_local: bool = False
    status: HostStatus = HostStatus.UNKNOWN
    last_check: float = 0.0
    last_error: str = ""
    # Authentication
    password: str = ""
    auto_deploy_key: bool = True
    key_passphrase: str = ""
    # SSH connection params (parsed from ~/.ssh/config)
    hostname: str = ""
    port: int = 22
    user: str = ""
    key_path: str = ""
    # Remote CLI preference
    remote_cli: RemoteCLI = RemoteCLI.OPENCODE


def _parse_ssh_config(alias: str) -> dict[str, Any]:
    """Parse SSH config for a given host alias."""
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.exists():
        return {}

    result: dict[str, Any] = {}
    current_host: str | None = None
    in_target = False

    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            if len(parts) < 2:
                continue

            key, value = parts[0].lower(), parts[1]

            if key == "host":
                hosts = value.split()
                in_target = alias in hosts
                continue

            if not in_target:
                continue

            if key == "hostname":
                result["hostname"] = value
            elif key == "port":
                result["port"] = int(value)
            elif key == "user":
                result["user"] = value
            elif key in ("identityfile", "key_path"):
                result["key_path"] = value.replace("~", str(Path.home()))

    return result


def _list_ssh_config_hosts() -> list[dict[str, Any]]:
    """List all hosts defined in ~/.ssh/config."""
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.exists():
        return []

    hosts = []
    current_block: dict[str, Any] = {}

    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            if len(parts) < 2:
                continue

            key, value = parts[0].lower(), parts[1]

            if key == "host":
                if current_block.get("aliases"):
                    hosts.append(current_block)
                aliases = value.split()
                current_block = {
                    "aliases": aliases,
                    "hostname": "",
                    "user": "",
                    "port": 22,
                }
                continue

            if not current_block:
                continue

            if key == "hostname":
                current_block["hostname"] = value
            elif key == "user":
                current_block["user"] = value
            elif key == "port":
                try:
                    current_block["port"] = int(value)
                except ValueError:
                    pass
            elif key in ("identityfile", "key_path"):
                current_block["key_path"] = value.replace("~", str(Path.home()))

    if current_block.get("aliases"):
        hosts.append(current_block)

    return hosts


def _find_hosts_config() -> Path | None:
    """Find project-level hosts.yaml with priority search.

    Search order:
    1. MAESTRO_HOSTS_PATH env var (if set and exists)
    2. MAESTRO_PROJECT_DIR/.maestro/hosts.yaml (if env var set and exists)
    3. Current working directory/.maestro/hosts.yaml (if exists)
    4. Return None to signal using global default
    """
    if path := os.environ.get("MAESTRO_HOSTS_PATH"):
        p = Path(path)
        if p.exists():
            logger.info(f"Using MAESTRO_HOSTS_PATH: {p}")
            return p

    if proj_dir := os.environ.get("MAESTRO_PROJECT_DIR"):
        p = Path(proj_dir) / ".maestro" / "hosts.yaml"
        if p.exists():
            logger.info(f"Using project-level hosts config (MAESTRO_PROJECT_DIR): {p}")
            return p

    p = Path.cwd() / ".maestro" / "hosts.yaml"
    if p.exists():
        logger.info(f"Using project-level hosts config (CWD): {p}")
        return p

    return None


def _load_hosts(config_path: Path | None = None) -> dict[str, HostConfig]:
    """Load host registry from hosts.yaml."""
    if config_path is None:
        config_path = _find_hosts_config()
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "hosts.yaml"
            logger.info(f"Using global default hosts config: {config_path}")
    if not config_path.exists():
        example = config_path.parent / "hosts.example.yaml"
        msg = f"Host config not found: {config_path}"
        if example.exists():
            msg += f"\n  Copy the example:  cp {example} {config_path}"
        raise SystemExit(msg)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or "hosts" not in raw:
        raise SystemExit(
            f"Invalid hosts.yaml: expected top-level 'hosts' key in {config_path}"
        )

    hosts: dict[str, HostConfig] = {}
    for name, cfg in raw["hosts"].items():
        if not isinstance(cfg, dict) or "alias" not in cfg:
            raise SystemExit(
                f"Invalid host '{name}' in {config_path}: 'alias' is required"
            )
        shell_str = cfg.get("shell", "bash").lower()
        try:
            shell = HostShell(shell_str)
        except ValueError:
            raise SystemExit(
                f"Invalid shell '{shell_str}' for host '{name}'. "
                f"Valid options: {', '.join(s.value for s in HostShell)}"
            )
        cli_str = cfg.get("remote_cli", "opencode").lower()
        try:
            remote_cli = RemoteCLI(cli_str)
        except ValueError:
            raise SystemExit(
                f"Invalid remote_cli '{cli_str}' for host '{name}'. "
                f"Valid options: {', '.join(s.value for s in RemoteCLI)}"
            )
        hosts[name] = HostConfig(
            alias=cfg["alias"],
            display_name=cfg.get("display_name", name),
            description=cfg.get("description", ""),
            shell=shell,
            is_local=cfg.get("is_local", False),
            password=cfg.get("password", ""),
            auto_deploy_key=cfg.get("auto_deploy_key", True),
            key_passphrase=cfg.get("key_passphrase", ""),
            remote_cli=remote_cli,
        )

        if not hosts[name].is_local:
            ssh_config = _parse_ssh_config(cfg["alias"])
            hosts[name].hostname = ssh_config.get("hostname", "")
            hosts[name].port = ssh_config.get("port", 22)
            hosts[name].user = ssh_config.get("user", "")
            hosts[name].key_path = ssh_config.get("key_path", "")

    if not hosts:
        raise SystemExit(f"No hosts defined in {config_path}")

    return hosts


# ---------------------------------------------------------------------------
# Module-level state (populated by init_hosts)
# ---------------------------------------------------------------------------

HOSTS: dict[str, HostConfig] = {}
_HOST_LOCKS: dict[str, asyncio.Lock] = {}


def init_hosts(config_path: Path | None = None) -> dict[str, HostConfig]:
    """Load hosts and initialise locks. Called once at import time from server.py."""
    loaded = _load_hosts(config_path)
    HOSTS.clear()
    HOSTS.update(loaded)
    _HOST_LOCKS.clear()
    _HOST_LOCKS.update({name: asyncio.Lock() for name in HOSTS})
    return HOSTS


async def _update_host_status(
    name: str,
    status: HostStatus,
    last_error: str = "",
) -> None:
    config = HOSTS[name]
    async with _HOST_LOCKS[name]:
        config.status = status
        config.last_check = time.time()
        if last_error:
            config.last_error = last_error


def _local_host_name() -> str | None:
    for name, config in HOSTS.items():
        if config.is_local:
            return name
    return None


def _resolve_host(host: str) -> HostConfig:
    if host not in HOSTS:
        available = ", ".join(sorted(HOSTS.keys()))
        raise ValueError(f"Unknown host '{host}'. Available hosts: {available}")
    return HOSTS[host]


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------


def _format_result(stdout: str, stderr: str, returncode: int) -> str:
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if returncode != 0:
        parts.append(f"[exit code: {returncode}]")
    return "\n".join(parts) or "[no output]"


def _ps_quote(value: str) -> str:
    """Quote a value for PowerShell using double quotes with backtick escaping."""
    escaped = value.replace("`", "``").replace('"', '`"').replace("$", "`$")
    return f'"{escaped}"'


def _wrap_command(config: HostConfig, command: str, cwd: str | None, sudo: bool) -> str:
    if config.shell == HostShell.POWERSHELL:
        parts = []
        if cwd:
            parts.append(f"Set-Location -LiteralPath {_ps_quote(cwd)};")
        parts.append(command)
        full = " ".join(parts)
        return f"sudo {full}" if sudo else full
    else:
        parts = []
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)} &&")
        if sudo:
            parts.append("sudo")
        parts.append(command)
        return " ".join(parts)
