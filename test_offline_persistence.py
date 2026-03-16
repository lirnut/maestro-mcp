#!/usr/bin/env python3
"""Test script for offline persistence functionality."""

import subprocess
import time
import json
import sys
from pathlib import Path


def run_cmd(cmd, check=True):
    """Run a shell command."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {cmd}")
        print(f"stderr: {result.stderr}")
        sys.exit(1)
    return result


def test_docker_running():
    """Test if Docker container is running."""
    result = run_cmd(
        "docker ps --filter name=maestro-test-remote --format '{{.Names}}'", check=False
    )
    if "maestro-test-remote" not in result.stdout:
        print("❌ Docker container not running. Run ./build-test-env.sh first.")
        return False
    print("✅ Docker container is running")
    return True


def test_ssh_connection():
    """Test SSH connection to the container."""
    result = run_cmd(
        "sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost 'echo connected'",
        check=False,
    )
    if "connected" not in result.stdout:
        print("❌ SSH connection failed")
        return False
    print("✅ SSH connection works")
    return True


def test_tmux_available():
    """Test if tmux is available in the container."""
    result = run_cmd(
        "sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost 'which tmux'",
        check=False,
    )
    if "tmux" not in result.stdout:
        print("❌ tmux not available in container")
        return False
    print("✅ tmux is available")
    return True


def test_opencode_available():
    """Test if OpenCode CLI is available."""
    result = run_cmd(
        "sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost 'export PATH=$PATH:~/.opencode/bin; opencode --version'",
        check=False,
    )
    if result.returncode != 0:
        print("❌ OpenCode CLI not available")
        return False
    print(f"✅ OpenCode CLI available: {result.stdout.strip()[:50]}")
    return True


def test_session_persistence():
    """Test session persistence with tmux."""
    session_name = "test-session-123"

    # Create a tmux session with a long-running command
    run_cmd(
        f"sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost "
        f"'tmux new-session -d -s {session_name} \"sleep 30 && echo done > /tmp/test-output.txt\"'"
    )
    print(f"✅ Created tmux session: {session_name}")

    # Verify session exists
    result = run_cmd(
        f"sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost "
        f"'tmux has-session -t {session_name} && echo exists'"
    )
    if "exists" not in result.stdout:
        print("❌ Session not found immediately after creation")
        return False
    print("✅ Session exists")

    # Wait a bit
    time.sleep(2)

    # Verify session still exists (persistence test)
    result = run_cmd(
        f"sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost "
        f"'tmux has-session -t {session_name} && echo exists'"
    )
    if "exists" not in result.stdout:
        print("❌ Session lost after short wait")
        return False
    print("✅ Session persisted")

    # Cleanup
    run_cmd(
        f"sshpass -p testpass ssh -o StrictHostKeyChecking=no -p 2222 testuser@localhost "
        f"'tmux kill-session -t {session_name} 2>/dev/null || true'"
    )

    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("Offline Persistence Tests")
    print("=" * 60)

    tests = [
        ("Docker Running", test_docker_running),
        ("SSH Connection", test_ssh_connection),
        ("tmux Available", test_tmux_available),
        ("OpenCode CLI", test_opencode_available),
        ("Session Persistence", test_session_persistence),
    ]

    results = []
    for name, test_fn in tests:
        print(f"\n--- Testing: {name} ---")
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"❌ Exception: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, p in results if p)
    total = len(results)

    for name, p in results:
        status = "✅ PASS" if p else "❌ FAIL"
        print(f"  {status}: {name}")

    print(f"\n{passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
