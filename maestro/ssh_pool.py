"""SSH connection pool manager using asyncssh.

Based on mcp-ssh-manager design patterns:
- Connection pooling with automatic validation
- Keepalive mechanism for long-lived connections
- Support for SSH agent, key + passphrase, and password auth
- Automatic reconnection on failure
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncssh

logger = logging.getLogger("maestro")

CONNECTION_TIMEOUT = 30 * 60
KEEPALIVE_INTERVAL = 5 * 60
PING_TIMEOUT = 5


@dataclass
class SSHConnection:
    conn: asyncssh.SSHClientConnection
    created_at: float
    last_used: float
    host_name: str = ""


@dataclass
class SSHConnectionParams:
    host: str
    port: int = 22
    user: str = ""
    password: str = ""
    key_path: str = ""
    key_passphrase: str = ""
    alias: str = ""
    proxy_jump: str = ""  # Jump host alias for ProxyJump support


class SSHConnectionPool:
    def __init__(self):
        self._connections: dict[str, SSHConnection] = {}
        self._keepalive_tasks: dict[str, asyncio.Task] = {}
        self._connect_locks: dict[str, asyncio.Lock] = {}

    async def get_connection(
        self,
        name: str,
        params: SSHConnectionParams,
    ) -> asyncssh.SSHClientConnection:
        if name in self._connections:
            ssh_conn = self._connections[name]
            try:
                if not ssh_conn.conn.is_closed():
                    ssh_conn.last_used = time.time()
                    return ssh_conn.conn
            except Exception:
                pass

        if name not in self._connect_locks:
            self._connect_locks[name] = asyncio.Lock()

        async with self._connect_locks[name]:
            if name in self._connections:
                ssh_conn = self._connections[name]
                if await self._is_valid(ssh_conn.conn):
                    ssh_conn.last_used = time.time()
                    logger.debug(f"Reusing existing connection to {name}")
                    return ssh_conn.conn
                else:
                    logger.info(f"Connection to {name} is stale, reconnecting...")
                    await self._close_connection_internal(name)

            logger.info(f"Creating new connection to {name}")
            conn = await self._create_connection(params)
            self._connections[name] = SSHConnection(
                conn=conn,
                created_at=time.time(),
                last_used=time.time(),
                host_name=name,
            )
            self._start_keepalive(name)
            return conn

    async def _create_connection(
        self, params: SSHConnectionParams
    ) -> asyncssh.SSHClientConnection:
        connect_kwargs: dict[str, Any] = {
            "host": params.host,
            "port": params.port,
            "username": params.user or None,
            "known_hosts": None,
            "connect_timeout": 30,
        }

        auth_methods = []

        if "SSH_AUTH_SOCK" in os.environ:
            connect_kwargs["agent_path"] = os.environ["SSH_AUTH_SOCK"]
            auth_methods.append("ssh-agent")

        if params.key_path:
            key_path = Path(params.key_path).expanduser()
            if key_path.exists():
                try:
                    keys = asyncssh.load_keypairs(
                        str(key_path),
                        passphrase=params.key_passphrase or None,
                    )
                    connect_kwargs["client_keys"] = keys
                    auth_methods.append(f"key:{key_path.name}")
                except asyncssh.KeyImportError:
                    pass

        if params.password:
            connect_kwargs["password"] = params.password
            auth_methods.append("password")

        if not auth_methods:
            connect_kwargs["client_host_keys"] = None

        # Handle ProxyJump
        jump_conn = None
        if params.proxy_jump:
            logger.info(
                f"Connecting to {params.host}:{params.port} via jump host {params.proxy_jump}"
            )
            from .hosts import HOSTS

            # Try exact match first, then case-insensitive
            jump_host = HOSTS.get(params.proxy_jump)
            if not jump_host:
                for name, host in HOSTS.items():
                    if name.lower() == params.proxy_jump.lower():
                        jump_host = host
                        break

            if jump_host:
                jump_params = SSHConnectionParams(
                    host=jump_host.hostname or jump_host.alias,
                    port=jump_host.port or 22,
                    user=jump_host.user or "",
                    password=jump_host.password or "",
                    key_path=jump_host.key_path or "",
                    key_passphrase=jump_host.key_passphrase or "",
                    alias=jump_host.alias,
                )
                jump_conn = await self._create_connection(jump_params)
                connect_kwargs["tunnel"] = jump_conn
            else:
                logger.warning(f"Jump host {params.proxy_jump} not found in hosts")

        logger.info(
            f"Connecting to {params.host}:{params.port} (auth: {', '.join(auth_methods) or 'default'})"
        )

        try:
            conn = await asyncssh.connect(**connect_kwargs)
            logger.info(f"Connected to {params.host}:{params.port}")
            return conn
        except asyncssh.HostKeyNotVerifiable:
            connect_kwargs["known_hosts"] = lambda *args: True
            conn = await asyncssh.connect(**connect_kwargs)
            logger.info(
                f"Connected to {params.host}:{params.port} (accepted new host key)"
            )
            return conn
        except Exception as e:
            # 如果认证失败且有密码，尝试只用密码认证
            if params.password and "permission" in str(e).lower():
                logger.info(
                    f"Key auth failed, falling back to password auth for {params.host}"
                )
                fallback_kwargs = {
                    "host": params.host,
                    "port": params.port,
                    "username": params.user or None,
                    "known_hosts": None,
                    "connect_timeout": 30,
                    "password": params.password,
                    "preferred_authentications": ["password"],
                }
                try:
                    conn = await asyncssh.connect(**fallback_kwargs)
                    logger.info(
                        f"Connected to {params.host}:{params.port} (password fallback)"
                    )
                    return conn
                except Exception as fallback_error:
                    logger.error(f"Password fallback also failed: {fallback_error}")
                    raise
            logger.error(f"Failed to connect to {params.host}:{params.port}: {e}")
            raise

    async def _is_valid(self, conn: asyncssh.SSHClientConnection) -> bool:
        try:
            result = await asyncio.wait_for(
                conn.run("echo ping", timeout=PING_TIMEOUT),
                timeout=PING_TIMEOUT + 1,
            )
            stdout = result.stdout
            if isinstance(stdout, bytes):
                stdout = stdout.decode()
            return result.exit_status == 0 and "ping" in (stdout or "")
        except Exception as e:
            logger.debug(f"Connection validation failed: {e}")
            return False

    def _start_keepalive(self, name: str):
        if name in self._keepalive_tasks:
            self._keepalive_tasks[name].cancel()

        async def keepalive_loop():
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if name not in self._connections:
                    break
                ssh_conn = self._connections[name]
                if not await self._is_valid(ssh_conn.conn):
                    logger.warning(f"Keepalive failed for {name}, closing connection")
                    await self.close_connection(name)
                    break
                logger.debug(f"Keepalive OK for {name}")

        self._keepalive_tasks[name] = asyncio.create_task(keepalive_loop())

    async def close_connection(self, name: str):
        if name not in self._connect_locks:
            self._connect_locks[name] = asyncio.Lock()
        async with self._connect_locks[name]:
            await self._close_connection_internal(name)

    async def _close_connection_internal(self, name: str):
        if name in self._keepalive_tasks:
            self._keepalive_tasks[name].cancel()
            del self._keepalive_tasks[name]

        if name in self._connections:
            ssh_conn = self._connections[name]
            try:
                ssh_conn.conn.close()
                await ssh_conn.conn.wait_closed()
            except Exception:
                pass
            del self._connections[name]
            logger.info(f"Closed connection to {name}")

    async def close_all(self):
        names = list(self._connections.keys())
        for name in names:
            await self.close_connection(name)

    async def run_command(
        self,
        name: str,
        params: SSHConnectionParams,
        command: str,
        timeout: int = 300,
        cwd: str | None = None,
        stdin_data: str | None = None,
    ) -> tuple[int, str, str]:
        conn = await self.get_connection(name, params)
        full_command = f"cd {cwd} && {command}" if cwd else command
        try:
            result = await asyncio.wait_for(
                conn.run(full_command, timeout=timeout, input=stdin_data),
                timeout=timeout + 10,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            return result.exit_status or 0, stdout, stderr
        except asyncio.TimeoutError:
            return -1, "", f"Command timeout after {timeout}s"
        except asyncssh.ProcessError as e:
            return e.exit_status or -1, "", str(e)

    async def put_file(
        self,
        name: str,
        params: SSHConnectionParams,
        local_path: str,
        remote_path: str,
        timeout: int = 300,
    ) -> tuple[bool, str]:
        conn = await self.get_connection(name, params)
        try:
            async with asyncio.timeout(timeout):
                async with conn.start_sftp_client() as sftp:
                    await sftp.put(local_path, remote_path)
            return True, ""
        except asyncio.TimeoutError:
            return False, f"SFTP upload timeout after {timeout}s"
        except Exception as e:
            logger.error(f"SFTP put failed: {e}")
            return False, str(e)

    async def get_file(
        self,
        name: str,
        params: SSHConnectionParams,
        remote_path: str,
        local_path: str,
        timeout: int = 300,
    ) -> tuple[bool, str]:
        conn = await self.get_connection(name, params)
        try:
            async with asyncio.timeout(timeout):
                async with conn.start_sftp_client() as sftp:
                    await sftp.get(remote_path, local_path)
            return True, ""
        except asyncio.TimeoutError:
            return False, f"SFTP download timeout after {timeout}s"
        except Exception as e:
            logger.error(f"SFTP get failed: {e}")
            return False, str(e)


_ssh_pool: SSHConnectionPool | None = None


def get_ssh_pool() -> SSHConnectionPool:
    global _ssh_pool
    if _ssh_pool is None:
        _ssh_pool = SSHConnectionPool()
    return _ssh_pool


async def close_ssh_pool():
    global _ssh_pool
    if _ssh_pool:
        await _ssh_pool.close_all()
        _ssh_pool = None
