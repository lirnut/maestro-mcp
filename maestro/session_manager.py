"""Session manager for persistent CLI sessions on remote hosts."""

from __future__ import annotations

import json
import os
import shlex
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from maestro.config import MaestroConfig


@dataclass
class SessionInfo:
    """Information about a persistent CLI session."""

    session_id: str
    agent: str
    prompt: str
    status: str  # "pending" | "running" | "completed" | "failed" | "timeout"
    created_at: str
    updated_at: str
    host: str
    tmux_session: str | None = None
    output_file: str | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionInfo:
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            agent=data["agent"],
            prompt=data["prompt"],
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            host=data["host"],
            tmux_session=data.get("tmux_session"),
            output_file=data.get("output_file"),
            exit_code=data.get("exit_code"),
        )


class RemoteSessionManager:
    """Manages persistent CLI sessions on a remote host."""

    def __init__(self, host: str, config: MaestroConfig):
        """
        Initialize session manager for a host.

        Args:
            host: Host name from fleet topology
            config: Maestro configuration
        """
        self.host = host
        self.config = config
        self._session_base_dir = Path.home() / ".maestro" / "sessions" / host

    def _session_dir(self) -> Path:
        """Get the base directory for sessions on this host."""
        return self._session_base_dir

    def _session_file_path(self, session_id: str) -> Path:
        """Get the file path for a session's JSON file."""
        return self._session_base_dir / f"{session_id}.json"

    def _generate_session_id(self) -> str:
        """Generate a unique session ID with 'maestro-' prefix."""
        return f"maestro-{uuid.uuid4().hex}"

    def _now(self) -> str:
        """Get current UTC time as ISO format string."""
        return datetime.now(timezone.utc).isoformat()

    def _save_session(self, session: SessionInfo) -> None:
        """
        Save session info to disk atomically.

        Args:
            session: SessionInfo to save
        """
        session_dir = self._session_dir()
        session_dir.mkdir(parents=True, exist_ok=True)

        session_file = self._session_file_path(session.session_id)
        temp_file = session_file.with_suffix(".json.tmp")

        try:
            # Write to temp file first
            temp_file.write_text(
                json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            # Atomic rename
            temp_file.rename(session_file)
        except OSError as e:
            # Clean up temp file if it exists
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Failed to save session {session.session_id}: {e}")

    def _load_session(self, session_id: str) -> SessionInfo | None:
        """
        Load session info from disk.

        Args:
            session_id: Session ID to load

        Returns:
            SessionInfo if found, None otherwise
        """
        session_file = self._session_file_path(session_id)

        if not session_file.exists():
            return None

        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return SessionInfo.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            raise RuntimeError(f"Failed to load session {session_id}: {e}")

    def create_session(
        self, agent: str, prompt: str, session_id: str | None = None
    ) -> str:
        """
        Create a new session.

        Args:
            agent: Agent type (codex, gemini, claude, opencode)
            prompt: User prompt for the session
            session_id: Optional session ID (auto-generated if not provided)

        Returns:
            Session ID
        """
        if session_id is None:
            session_id = self._generate_session_id()

        now = self._now()
        session = SessionInfo(
            session_id=session_id,
            agent=agent,
            prompt=prompt,
            status="pending",
            created_at=now,
            updated_at=now,
            host=self.host,
            tmux_session=None,
            output_file=None,
            exit_code=None,
        )

        self._save_session(session)
        return session_id

    def get_session(self, session_id: str) -> SessionInfo | None:
        """
        Get session info by ID.

        Args:
            session_id: Session ID

        Returns:
            SessionInfo if found, None otherwise
        """
        return self._load_session(session_id)

    def update_session(self, session_id: str, **kwargs) -> None:
        """
        Update session info.

        Args:
            session_id: Session ID
            **kwargs: Fields to update (status, tmux_session, output_file, exit_code)
        """
        session = self._load_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")

        # Update fields if provided
        now = self._now()
        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.updated_at = now

        self._save_session(session)

    def list_sessions(self, status: str | None = None) -> list[SessionInfo]:
        """
        List sessions, optionally filtered by status.

        Args:
            status: Optional status filter

        Returns:
            List of SessionInfo objects
        """
        sessions = []

        if not self._session_dir().exists():
            return sessions

        for session_file in self._session_dir().glob("*.json"):
            if session_file.suffix != ".json":
                continue

            # Skip temp files
            if session_file.name.endswith(".json.tmp"):
                continue

            try:
                session = self._load_session(session_file.stem)
                if session is not None:
                    if status is None or session.status == status:
                        sessions.append(session)
            except RuntimeError:
                # Skip corrupted session files
                continue

        # Sort by created_at descending (newest first)
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session ID

        Returns:
            True if session was deleted, False if not found
        """
        session_file = self._session_file_path(session_id)

        if not session_file.exists():
            return False

        try:
            session_file.unlink()
            return True
        except OSError:
            return False

    def _build_cli_command(self, session: SessionInfo) -> str:
        """
        Build CLI command for the given session.

        Args:
            session: SessionInfo with agent and prompt

        Returns:
            Shell command string with PATH fix and proper escaping
        """
        agent = session.agent
        prompt = session.prompt

        # PATH fix for user-local binary installations
        path_fix = "export PATH=$PATH:~/.local/bin:~/bin:~/.opencode/bin 2>/dev/null; "

        # Build agent-specific command
        if agent == "opencode":
            # OpenCode CLI - JSON format output
            cmd = f"{path_fix}opencode run {shlex.quote(prompt)} --format json"
        elif agent == "codex":
            # Codex CLI - JSON format with bypass approvals
            cmd = (
                f"{path_fix}codex exec --dangerously-bypass-approvals-and-sandbox --json "
                f"-C {shlex.quote(str(Path.home()))} {shlex.quote(prompt)}"
            )
        elif agent == "gemini":
            # Gemini CLI - JSON output format
            cmd = f"{path_fix}gemini -p {shlex.quote(prompt)} --output-format json"
        elif agent == "claude":
            # Claude Code CLI - JSON output with bypass permissions
            cmd = (
                f"{path_fix}claude -p {shlex.quote(prompt)} --output-format json "
                f"--permission-mode bypassPermissions"
            )
        else:
            # Default: treat as opencode
            cmd = f"{path_fix}opencode run {shlex.quote(prompt)} --format json"

        return cmd

    async def start_session(self, session_id: str, exec_fn: Callable) -> bool:
        """
        Start a tmux session for the CLI process.

        Args:
            session_id: Session ID
            exec_fn: Callable that executes command on remote host

        Returns:
            True if session started successfully, False otherwise
        """
        session = self._load_session(session_id)
        if session is None:
            return False

        # Build the CLI command
        cli_cmd = self._build_cli_command(session)

        # Output file for session results
        output_file = self._session_base_dir / f"{session_id}.out"
        session.output_file = str(output_file)

        # Create tmux session name
        tmux_session_name = f"maestro-{session_id}"

        # Command to run in tmux: save output to file, then exit
        tmux_cmd = (
            f"bash -c '{cli_cmd} 2>&1 | tee {shlex.quote(str(output_file))}; "
            f"exit_code=$?; "
            f"tmux kill-session -t {shlex.quote(tmux_session_name)} || true; "
            f"exit $exit_code'"
        )

        # Start tmux session with the command
        start_tmux_cmd = (
            f"tmux new-session -d -s {shlex.quote(tmux_session_name)} '{tmux_cmd}'"
        )

        try:
            # Execute the tmux start command
            result = await exec_fn(host=self.host, command=start_tmux_cmd)
            session.tmux_session = tmux_session_name
            session.status = "running"
            session.updated_at = self._now()
            self._save_session(session)
            return True
        except Exception:
            session.status = "failed"
            session.updated_at = self._now()
            self._save_session(session)
            return False

    async def check_session_status(self, session_id: str, exec_fn: Callable) -> str:
        """
        Check if tmux session is still running.

        Args:
            session_id: Session ID
            exec_fn: Callable that executes command on remote host

        Returns:
            Current status: "running", "completed", "failed", or "unknown"
        """
        session = self._load_session(session_id)
        if session is None:
            return "unknown"

        tmux_session_name = session.tmux_session
        if not tmux_session_name:
            # No tmux session, check if output file exists (completed)
            if session.output_file and Path(session.output_file).exists():
                return "completed"
            return "failed"

        # Check if tmux session exists
        check_cmd = f"tmux has-session -t {shlex.quote(tmux_session_name)} 2>/dev/null && echo exists || echo absent"
        result = await exec_fn(host=self.host, command=check_cmd)

        if "exists" in result:
            session.status = "running"
            session.updated_at = self._now()
            self._save_session(session)
            return "running"

        # Session not running, check output file
        if session.output_file and Path(session.output_file).exists():
            session.status = "completed"
            session.updated_at = self._now()
            self._save_session(session)
            return "completed"

        session.status = "failed"
        session.updated_at = self._now()
        self._save_session(session)
        return "failed"

    async def capture_output(self, session_id: str, exec_fn: Callable) -> str | None:
        """
        Capture output from tmux session non-intrusively.

        Args:
            session_id: Session ID
            exec_fn: Callable that executes command on remote host

        Returns:
            Captured output or None if session not found
        """
        session = self._load_session(session_id)
        if session is None:
            return None

        tmux_session_name = session.tmux_session
        if not tmux_session_name:
            return None

        # Capture tmux pane content
        capture_cmd = f"tmux capture-pane -t {shlex.quote(tmux_session_name)} -p"
        result = await exec_fn(host=self.host, command=capture_cmd)

        # Check if session exists (empty result might mean session not found)
        if (
            "failed to locate session" in result.lower()
            or "no such session" in result.lower()
        ):
            return None

        return result

    async def kill_session_process(self, session_id: str, exec_fn: Callable) -> bool:
        """
        Kill the tmux session.

        Args:
            session_id: Session ID
            exec_fn: Callable that executes command on remote host

        Returns:
            True if session was killed, False otherwise
        """
        session = self._load_session(session_id)
        if session is None:
            return False

        tmux_session_name = session.tmux_session
        if not tmux_session_name:
            return False

        # Kill tmux session
        kill_cmd = f"tmux kill-session -t {shlex.quote(tmux_session_name)}"
        try:
            await exec_fn(host=self.host, command=kill_cmd)
            session.status = "failed"
            session.updated_at = self._now()
            self._save_session(session)
            return True
        except Exception:
            return False

    async def sync_session_states(self, exec_fn: Callable) -> list[SessionInfo]:
        """
        Synchronize session states with actual tmux sessions on the host.
        Called on host reconnection to detect orphaned/completed sessions.

        Args:
            exec_fn: Callable that executes command on remote host

        Returns:
            List of updated sessions
        """
        updated_sessions = []

        # Get all sessions from disk
        all_sessions = self.list_sessions()

        for session in all_sessions:
            if session.status not in ("running", "pending"):
                continue

            old_status = session.status

            # Check if tmux session exists
            if session.tmux_session:
                check_cmd = f"tmux has-session -t {shlex.quote(session.tmux_session)} 2>/dev/null && echo exists || echo absent"
                try:
                    result = await exec_fn(host=self.host, command=check_cmd)
                    if "exists" in result:
                        session.status = "running"
                    else:
                        # Tmux session gone, check output file
                        if session.output_file:
                            output_path = Path(session.output_file)
                            if output_path.exists():
                                session.status = "completed"
                            else:
                                session.status = "failed"
                        else:
                            session.status = "failed"
                except Exception:
                    session.status = "failed"

            if session.status != old_status:
                session.updated_at = self._now()
                self._save_session(session)
                updated_sessions.append(session)

        return updated_sessions

    async def recover_session(self, session_id: str, exec_fn: Callable) -> bool:
        """
        Attempt to recover a failed session by restarting it.

        Args:
            session_id: Session ID to recover
            exec_fn: Callable that executes command on remote host

        Returns:
            True if recovery successful, False otherwise
        """
        session = self._load_session(session_id)
        if session is None:
            return False

        if session.status not in ("failed", "pending"):
            return False

        # Reset session state and restart
        session.status = "pending"
        session.tmux_session = None
        session.exit_code = None
        self._save_session(session)

        # Attempt to start again
        return await self.start_session(session_id, exec_fn)
