# Maestro MCP — Gemini CLI Guide

Maestro integrates with Gemini CLI to provide a unified interface for managing a fleet of machines and dispatching asynchronous agent tasks.

## Getting Started

To add Maestro to your Gemini CLI session:

```bash
gemini mcp add maestro /path/to/maestro-mcp/.venv/bin/python /path/to/maestro-mcp/server.py --transport stdio
```

## Core Fleet Workflows

### 1. Unified Execution
Run commands across any machine in your fleet. Maestro handles SSH persistence and shell differences (Bash/PowerShell).

```bash
# Check status of all hosts
gemini mcp call maestro status

# Run a command on a specific host
gemini mcp call maestro exec --host gpu-box --command "nvidia-smi"
```

### 2. Zero-Context File Transfers
Move files between machines without bloating your LLM context.

```bash
gemini mcp call maestro transfer --host macbook --direction upload --local_path "./src/main.py" --remote_path "~/workspace/main.py"
```

### 3. Surgical Reads
Avoid context exhaustion by reading only what you need.

```bash
# Read the last 50 lines of a log file on a remote host
gemini mcp call maestro read --host linux-box --path "/var/log/syslog" --tail 50
```

## Agent Orchestra

Maestro allows you to dispatch tasks to Gemini CLI as background processes.

### Dispatching a Task
Use the `gemini` tool to start a task.

```bash
gemini mcp call maestro gemini --host workstation --prompt "Refactor the authentication logic" --approval_mode yolo
```

**Parameters:**
- `approval_mode`: `plan` (read-only), `yolo` (auto-approve), `auto_edit` (auto-approve edits), `default`.
- `resume`: Session index or "latest" to continue a previous chat.
- `context_files`: List of `@file` references to include.

### Session Management
List previous sessions on a host to find an index for `resume`:
```bash
gemini mcp call maestro gemini_sessions --host workstation
```

### Task Lifecycle
1. **Dispatch:** Returns a `task_id`.
2. **Poll:** Check the status of the task.
   ```bash
   gemini mcp call maestro poll --task_id <id>
   ```
3. **Retrieve:** Read the output once complete.
   ```bash
   gemini mcp call maestro read_output --file_path <output_path_from_poll>
   ```

## Best Practices & Warnings

- **Token Costs:** Resuming a session (`resume` parameter) re-sends the entire history. You pay for all previous turn tokens as input.
- **Auto-Promote:** Long-running `exec` or `gemini` calls automatically become background tasks.
- **Sensitive Data:** Never commit `hosts.yaml` or `.env` to public repositories.
