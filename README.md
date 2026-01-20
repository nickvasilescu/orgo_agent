# Agent Execution Runtime

A cloud execution layer for AI agents that accepts natural language plans and executes them on persistent cloud VMs.

## Features

- **Workspace Management** - Map git repos to persistent Orgo VMs
- **Plan Execution** - Submit natural language plans for async execution
- **Git-based Sync** - Results committed and pushed to branches
- **MCP Interface** - 4 tools callable by any LLM via MCP

## Quick Start

### 1. Install

```bash
pip install -e .
```

### 2. Configure Environment

```bash
export ORGO_API_KEY=your_orgo_api_key
```

### 3. Add to Claude Code

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "workspace": {
      "command": "python",
      "args": ["-m", "workspace_mcp.server"],
      "cwd": "/path/to/orgo_agent"
    }
  }
}
```

### 4. Use the Tools

```
# Register a workspace
workspace_register(
  name="my-project",
  git_remote="https://github.com/user/repo.git",
  git_token="ghp_xxx",
  anthropic_api_key="sk-ant-xxx"
)

# Submit a plan
plan_submit(
  workspace_id="my-project",
  plan="Add unit tests for the auth module"
)

# Check status
plan_status(plan_id="abc123")

# Get merge instructions
workspace_sync(workspace_id="my-project")
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     USER'S LOCAL MACHINE                        │
│  ┌─────────────┐    ┌─────────────────────────────────────┐    │
│  │ Claude Code │───▶│ Workspace MCP Server                │    │
│  │   (LLM)     │    │  • workspace_register               │    │
│  │             │◀───│  • plan_submit                      │    │
│  └─────────────┘    │  • plan_status                      │    │
│                     │  • workspace_sync                   │    │
│                     └──────────────┬──────────────────────┘    │
│                                    │                            │
│                     ┌──────────────▼──────────────────────┐    │
│                     │ Orgo MCP Server                     │    │
│                     └──────────────┬──────────────────────┘    │
└────────────────────────────────────┼────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ORGO CLOUD                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Persistent Workspace VM                     │   │
│  │                                                          │   │
│  │  ┌─────────────────────────────────────────────────┐    │   │
│  │  │  RALPH WIGGUM AGENT                             │    │   │
│  │  │  • Anthropic API with tool use                  │    │   │
│  │  │  • browser-use for web automation               │    │   │
│  │  │  • File read/write + bash                       │    │   │
│  │  │  • Git sync (pull/push)                         │    │   │
│  │  └─────────────────────────────────────────────────┘    │   │
│  │                                                          │   │
│  │  ~/workspace/  ← cloned from user's git repo             │   │
│  │  tasks.md      ← task queue (Ralph polls this)           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Workspace Types

Ralph Wiggum auto-detects workspace type and provides appropriate tools:

| Type | Detection | Available Commands |
|------|-----------|-------------------|
| Node.js | `package.json` | npm install, npm test, npm run build |
| Python | `pyproject.toml` or `requirements.txt` | pip install, pytest, ruff |
| Obsidian | `.obsidian/` | Markdown-focused operations |
| Generic | (default) | Basic file/bash operations |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run specific test class
pytest tests/test_e2e.py::TestStateManagement -v
```

## License

MIT
