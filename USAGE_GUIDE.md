# Maestro MCP Usage Guide

## Overview

Maestro is a multi-host machine fleet orchestration layer. It turns SSH-accessible machines into a unified workspace.

## Quick Start

1. **Check available hosts**: Call `status()` to see which hosts are connected
2. **Run commands**: Use `exec(host, command)` to run shell commands
3. **Deploy agents**: Use `run(host, prompt)` to dispatch AI tasks

## Tool Selection Guide

### When to use each tool:

| I want to... | Use this tool |
|--------------|---------------|
| Run a shell command | `exec(host, command)` |
| Run a multi-line script | `script(host, script)` |
| Read a remote file | `read(host, path)` |
| Write to a remote file | `write(host, path, content)` |
| Transfer files | `transfer(host, direction, local_path, remote_path)` |
| Check host connectivity | `status()` |
| Check CLI availability | `agent_status(host)` |
| Install a CLI agent | `install_agent(host, agent)` |
| Run an AI task | `run(host, prompt)` |

### Host Selection

The `host` parameter must match a name in `hosts.yaml`. Call `status()` first to see available hosts.

Common hosts:
- `local` - The machine running Maestro (no SSH)
- Other hosts defined in your fleet configuration

### CLI Agents

Maestro supports multiple AI CLI tools:
- `opencode` - Default, general-purpose
- `codex` - OpenAI's coding agent
- `gemini` - Google's AI assistant
- `claude` - Anthropic's Claude CLI

Use `agent_status(host)` to check which CLIs are installed.

## Common Workflows

### 1. Check fleet status
```
status()  → Returns connectivity of all hosts
```

### 2. Run a quick command
```
exec(host="my-server", command="docker ps")
```

### 3. Read a config file
```
read(host="my-server", path="/etc/nginx/nginx.conf")
```

### 4. Deploy and run an AI task
```
agent_status(host="my-server")  # Check CLI availability
run(host="my-server", prompt="Fix the TypeScript errors in src/")
```

### 5. Install a CLI if not available
```
install_agent(host="my-server", agent="opencode")
```

## Error Handling

- **Permission denied**: Host needs password in `hosts.yaml`
- **Connection timeout**: Host may be offline or network unreachable
- **CLI not available**: Call `install_agent()` to install

## Persistent Sessions

For long-running tasks that survive disconnection:
- `create_persistent_session(host, agent, prompt)` - Start a session
- `get_persistent_session(host, session_id)` - Check status
- `list_persistent_sessions(host)` - List all sessions