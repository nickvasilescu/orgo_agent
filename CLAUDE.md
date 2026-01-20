# CLAUDE.md

## Project Overview

Agent Execution Runtime - A cloud execution layer for AI agents that:
- Accepts natural language plans from any LLM
- Executes them on persistent cloud VMs (Orgo) against user workspaces (git repos)
- Syncs results back via git branches while user is offline
- Exposes everything as MCP tools (LLM-agnostic)

## Tech Stack

- **Python 3.10+** - Main language
- **MCP (Model Context Protocol)** - For exposing tools to LLMs
- **Orgo** - Cloud VM infrastructure
- **Anthropic API** - Powers the Ralph Wiggum agent on VMs
- **Pydantic** - Input validation
- **browser-use / playwright** - Web automation on VMs

## Project Structure

```
orgo_agent/
├── workspace_mcp/           # MCP server package
│   ├── __init__.py
│   ├── server.py            # MCP server with 4 tools
│   └── state.py             # JSON-based state management
├── agent/                   # Agent code deployed to VMs
│   ├── ralph_wiggum.py      # The AI agent (runs on VM)
│   └── bootstrap.sh         # VM setup script
├── tests/
│   └── test_e2e.py          # End-to-end tests
├── pyproject.toml
├── requirements.txt
└── CLAUDE.md
```

## Commands

```bash
# Install dependencies
pip install -e .

# Run MCP server (for Claude Code integration)
python -m workspace_mcp.server

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ -v --cov=workspace_mcp
```

## MCP Tools

This project provides 4 MCP tools:

| Tool | Description |
|------|-------------|
| `workspace_register` | Register a git repo as a workspace, bootstrap VM with Ralph Wiggum |
| `plan_submit` | Submit a natural language plan for async execution |
| `plan_status` | Check status of a running plan |
| `workspace_sync` | Get sync status and merge instructions |

## Key Files

- `workspace_mcp/server.py` - MCP server entry point with all 4 tools
- `workspace_mcp/state.py` - State management (workspaces + plans)
- `agent/ralph_wiggum.py` - The AI agent deployed to VMs
- `agent/bootstrap.sh` - VM bootstrap script

## Testing

```bash
# Unit tests (no API keys needed)
pytest tests/test_e2e.py::TestStateManagement -v
pytest tests/test_e2e.py::TestRalphWiggum -v

# Integration tests (requires API keys)
export ORGO_API_KEY=xxx
export ANTHROPIC_API_KEY=xxx
export TEST_GIT_TOKEN=xxx
export TEST_GIT_REMOTE=https://github.com/user/test-repo.git
pytest tests/test_e2e.py::TestE2EFlow -v
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ORGO_API_KEY` | Orgo API key for VM management |
| `ANTHROPIC_API_KEY` | Anthropic API key (passed to VMs for Ralph) |

## Conventions

- State is stored in `~/.workspace-mcp/` (JSON files)
- Workspaces are keyed by name (unique identifier)
- Plans are keyed by auto-generated UUID
- Agent commits go to `agent/{uuid}` branches
- Ralph Wiggum polls `~/workspace/tasks.md` for new tasks

## Architecture Flow

```
1. workspace_register(name, git_remote, token)
   → Creates Orgo VM
   → Clones repo
   → Deploys Ralph Wiggum agent

2. plan_submit(workspace_id, plan)
   → Creates branch: agent/{uuid}
   → Appends task to tasks.md
   → Ralph picks up and executes

3. plan_status(plan_id)
   → Checks Ralph logs
   → Checks git commits
   → Returns progress

4. workspace_sync(workspace_id)
   → Lists agent branches
   → Returns merge instructions
```

## Warnings

- **API keys on VM**: Currently stored in `~/.env` - consider secrets manager for production
- **No VM image caching**: Bootstrap runs every time - could pre-bake images later
- **Single task at a time**: Ralph processes tasks sequentially from tasks.md
- **Git auth**: Uses PAT embedded in clone URL - expires based on token settings
