"""SSH transport layer using asyncssh connection pool."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from maestro.config import MaestroConfig
from maestro.hosts import HostStatus
from maestro.ssh_pool import SSHConnectionParams, get_ssh_pool, close_ssh_pool

logger = logging.getLogger("maestro")

_ResolveHost = Any
_UpdateHostStatus = Any

_CONFIG: MaestroConfig | None = None
_HOSTS: dict[str, Any] = {}
_RESOLVE_HOST: _ResolveHost | None = None
_UPDATE_HOST_STATUS: _UpdateHostStatus | None = None
_FORMAT_RESULT: Any = None


def configure_transport(
    config: MaestroConfig,
    hosts: dict[str, Any],
    locks: dict[str, Any],
    update_host_status: _UpdateHostStatus,
    resolve_host: _ResolveHost,
    format_result: Any,
) -> None:
    global _CONFIG, _HOSTS, _RESOLVE_HOST, _UPDATE_HOST_STATUS, _FORMAT_RESULT
    _CONFIG = config
    _HOSTS = hosts
    _RESOLVE_HOST = resolve_host
    _UPDATE_HOST_STATUS = update_host_status
    _FORMAT_RESULT = format_result


def _require_config() -> MaestroConfig:
    if _CONFIG is None:
        raise RuntimeError("transport not configured")
    return _CONFIG


def _get_ssh_params(host_config: Any) -> SSHConnectionParams:
    return SSHConnectionParams(
        host=host_config.hostname or host_config.alias,
        port=host_config.port or 22,
        user=host_config.user or "",
        password=host_config.password or "",
        key_path=host_config.key_path or "",
        key_passphrase=host_config.key_passphrase or "",
        alias=host_config.alias,
    )


async def warmup_all_hosts() -> dict[str, bool]:
    results = {}
    pool = get_ssh_pool()

    async def _warmup_one(name: str, config: Any) -> tuple[str, bool]:
        if config.is_local:
            config.status = HostStatus.CONNECTED
            logger.info(f"{name}: local host")
            return name, True

        try:
            params = _get_ssh_params(config)
            await pool.get_connection(name, params)
            config.status = HostStatus.CONNECTED
            logger.info(f"{name}: connected")
            return name, True
        except Exception as e:
            config.status = HostStatus.DISCONNECTED
            config.last_error = str(e)
            logger.warning(f"{name}: connection failed - {e}")
            return name, False

    tasks = [_warmup_one(name, cfg) for name, cfg in _HOSTS.items()]
    pairs = await asyncio.gather(*tasks)
    return dict(pairs)


async def teardown_all_hosts() -> None:
    await close_ssh_pool()
    for name, config in _HOSTS.items():
        if not config.is_local:
            config.status = HostStatus.DISCONNECTED
    logger.info("All SSH connections closed")


async def _ssh_run(
    host_name: str,
    ssh_args: list[str],
    timeout: int = 300,
    stdin_data: str | None = None,
) -> str:
    config = _RESOLVE_HOST(host_name)
    if config.is_local:
        raise RuntimeError("Use local execution for local hosts")

    pool = get_ssh_pool()
    params = _get_ssh_params(config)
    command = " ".join(ssh_args)

    try:
        rc, stdout, stderr = await pool.run_command(
            host_name, params, command, timeout=timeout, stdin_data=stdin_data
        )
        config.status = HostStatus.CONNECTED
        return _FORMAT_RESULT(stdout, stderr, rc)
    except Exception as e:
        config.status = HostStatus.ERROR
        config.last_error = str(e)
        return f"[SSH error on {host_name}]\n{e}"


async def _scp_run(
    host_name: str,
    source: str,
    destination: str,
    upload: bool = True,
    timeout: int = 300,
) -> str:
    config = _RESOLVE_HOST(host_name)
    if config.is_local:
        raise RuntimeError("Use local file operations for local hosts")

    pool = get_ssh_pool()
    params = _get_ssh_params(config)

    try:
        if upload:
            success, error = await pool.put_file(
                host_name, params, source, destination, timeout
            )
            action = f"upload {source} -> {host_name}:{destination}"
        else:
            success, error = await pool.get_file(
                host_name, params, source, destination, timeout
            )
            action = f"download {host_name}:{source} -> {destination}"

        if success:
            config.status = HostStatus.CONNECTED
            return f"[OK] {action}"
        else:
            return f"[SFTP failed] {action}: {error}"
    except Exception as e:
        config.status = HostStatus.ERROR
        config.last_error = str(e)
        return f"[SFTP error on {host_name}]\n{e}"


async def _ensure_connection(alias: str, host_name: str) -> bool:
    config = _RESOLVE_HOST(host_name)
    if config.is_local:
        return True

    pool = get_ssh_pool()
    params = _get_ssh_params(config)
    try:
        await pool.get_connection(host_name, params)
        return True
    except Exception as e:
        logger.warning(f"Connection failed for {host_name}: {e}")
        return False


async def _teardown_connection(alias: str) -> None:
    pool = get_ssh_pool()
    await pool.close_connection(alias)


TRANSIENT_INDICATORS = [
    "Connection refused",
    "Connection timed out",
    "Connection reset",
    "Broken pipe",
    "No route to host",
    "Network is unreachable",
    "ssh_exchange_identification",
    "Connection closed by remote host",
]


def _is_transient_failure(returncode: int, stderr: str) -> bool:
    if returncode not in (-1, 255):
        return False
    return any(ind in stderr for ind in TRANSIENT_INDICATORS)


async def _async_run(
    args: list[str],
    timeout: int = 300,
    stdin_data: str | None = None,
) -> tuple[int, str, str]:
    """Execute a command, using SSH pool for ssh commands or subprocess for others."""
    if args and args[0] == "ssh" and len(args) >= 2:
        alias = args[1]
        command = " ".join(args[2:]) if len(args) > 2 else "true"

        host_name = None
        for name, cfg in _HOSTS.items():
            if hasattr(cfg, "alias") and cfg.alias == alias:
                host_name = name
                break

        if host_name:
            config = _HOSTS[host_name]
            if config.is_local:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
                return (
                    proc.returncode or 0,
                    stdout_bytes.decode(errors="replace"),
                    stderr_bytes.decode(errors="replace"),
                )

            pool = get_ssh_pool()
            params = _get_ssh_params(config)
            try:
                rc, stdout, stderr = await pool.run_command(
                    host_name, params, command, timeout=timeout
                )
                return rc, stdout, stderr
            except asyncio.TimeoutError:
                return -1, "", f"timeout after {timeout}s"
            except Exception as e:
                return -1, "", str(e)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(input=stdin_data.encode() if stdin_data else None),
        timeout=timeout,
    )
    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )
