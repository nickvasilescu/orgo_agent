"""
Workspace MCP Server

Provides 4 tools for AI agent workspace management:
1. workspace_register - Register a git repo as a workspace on an Orgo VM
2. plan_submit - Submit a natural language plan for execution
3. plan_status - Check status of a running plan
4. workspace_sync - Get sync status and instructions
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

from .state import Plan, StateStore, Workspace, get_state

# Load environment variables
load_dotenv()

# Create MCP server
server = Server("workspace-mcp")

# Agent code to upload to VMs
RALPH_WIGGUM_PATH = Path(__file__).parent.parent / "agent" / "ralph_wiggum.py"
BOOTSTRAP_SCRIPT_PATH = Path(__file__).parent.parent / "agent" / "bootstrap.sh"


# Input models for tools
class WorkspaceRegisterInput(BaseModel):
    """Input for workspace_register tool."""

    name: str = Field(description="Unique name for this workspace (e.g., 'my-project')")
    git_remote: str = Field(description="Git remote URL (e.g., 'https://github.com/user/repo.git')")
    git_token: str = Field(description="GitHub Personal Access Token for push access")
    anthropic_api_key: str = Field(description="Anthropic API key for the Ralph Wiggum agent")
    branch: str = Field(default="main", description="Branch to clone (default: main)")
    vm_id: Optional[str] = Field(default=None, description="Existing Orgo VM ID (creates new if not provided)")


class PlanSubmitInput(BaseModel):
    """Input for plan_submit tool."""

    workspace_id: str = Field(description="Workspace name/ID from workspace_register")
    plan: str = Field(description="Natural language plan to execute")
    branch_name: Optional[str] = Field(default=None, description="Branch name for results (default: auto-generated)")


class PlanStatusInput(BaseModel):
    """Input for plan_status tool."""

    plan_id: str = Field(description="Plan ID from plan_submit")


class WorkspaceSyncInput(BaseModel):
    """Input for workspace_sync tool."""

    workspace_id: str = Field(description="Workspace name/ID")


async def run_orgo_bash(computer_id: str, command: str) -> dict:
    """Run a bash command on an Orgo computer using the SDK."""
    try:
        from orgo import Computer

        computer = Computer(computer_id=computer_id)
        result = computer.bash(command)
        return {"success": True, "output": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def upload_file_to_vm(computer_id: str, local_path: Path, remote_path: str) -> dict:
    """Upload a file to an Orgo VM."""
    try:
        from orgo import Computer
        import base64

        computer = Computer(computer_id=computer_id)

        # Read file content
        with open(local_path, "r") as f:
            content = f.read()

        # Write via bash (for text files)
        # Escape special characters for bash
        escaped_content = content.replace("\\", "\\\\").replace("'", "'\\''")

        # Use cat with heredoc for large files
        result = computer.bash(f"cat > {remote_path} << 'HEREDOC_EOF'\n{content}\nHEREDOC_EOF")

        return {"success": True, "path": remote_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="workspace_register",
            description="""Register a git repository as a workspace and bootstrap an Orgo VM with the Ralph Wiggum agent.

This creates a persistent cloud VM with:
- The git repo cloned with push access
- Ralph Wiggum agent installed and running
- All dependencies (browser-use, playwright, etc.)

The workspace is ready for plan submission after registration.""",
            inputSchema=WorkspaceRegisterInput.model_json_schema(),
        ),
        Tool(
            name="plan_submit",
            description="""Submit a natural language plan for execution by the Ralph Wiggum agent.

The agent will:
1. Read and understand the plan
2. Execute it step by step using available tools
3. Commit changes to a new branch
4. Push results to the remote repository

Check status with plan_status and sync results with workspace_sync.""",
            inputSchema=PlanSubmitInput.model_json_schema(),
        ),
        Tool(
            name="plan_status",
            description="""Check the status of a submitted plan.

Returns:
- Current status (queued, running, completed, failed)
- Recent agent logs
- Commits made so far
- Any errors encountered""",
            inputSchema=PlanStatusInput.model_json_schema(),
        ),
        Tool(
            name="workspace_sync",
            description="""Get sync status and instructions for a workspace.

Returns:
- Available branches with agent changes
- Instructions to merge changes locally
- Recent commits on agent branches""",
            inputSchema=WorkspaceSyncInput.model_json_schema(),
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""

    if name == "workspace_register":
        return await handle_workspace_register(WorkspaceRegisterInput(**arguments))
    elif name == "plan_submit":
        return await handle_plan_submit(PlanSubmitInput(**arguments))
    elif name == "plan_status":
        return await handle_plan_status(PlanStatusInput(**arguments))
    elif name == "workspace_sync":
        return await handle_workspace_sync(WorkspaceSyncInput(**arguments))
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def handle_workspace_register(input: WorkspaceRegisterInput) -> list[TextContent]:
    """Handle workspace_register tool call."""
    state = get_state()

    # Check if workspace already exists
    existing = state.get_workspace(input.name)
    if existing:
        return [
            TextContent(
                type="text",
                text=f"Workspace '{input.name}' already exists with VM ID: {existing.vm_id}\n"
                f"URL: {existing.url}\nStatus: {existing.status}",
            )
        ]

    try:
        from orgo import Computer

        # Create or connect to VM
        if input.vm_id:
            computer = Computer(computer_id=input.vm_id)
            vm_id = input.vm_id
        else:
            # Create new computer in "agent-workspaces" project
            computer = Computer(project="agent-workspaces", name=input.name)
            vm_id = computer.computer_id

        # Get VM URL
        vm_url = f"https://orgo.ai/computers/{vm_id}"

        # Create workspace record
        workspace = Workspace(
            name=input.name,
            vm_id=vm_id,
            git_remote=input.git_remote,
            branch=input.branch,
            status="bootstrapping",
            url=vm_url,
        )
        state.save_workspace(workspace)

        # Run bootstrap script
        bootstrap_commands = [
            # Update system
            "apt-get update -qq",
            "apt-get install -y -qq python3-pip python3-venv git curl wget xvfb",
            # Create virtual environment
            "python3 -m venv ~/agent_env",
            # Install dependencies
            "~/agent_env/bin/pip install --upgrade pip -q",
            "~/agent_env/bin/pip install -q anthropic browser-use playwright langchain-anthropic pydantic httpx python-dotenv",
            # Install playwright browsers
            "~/agent_env/bin/playwright install chromium",
            "~/agent_env/bin/playwright install-deps chromium || true",
            # Create directories
            "mkdir -p ~/workspace ~/logs ~/vault",
        ]

        for cmd in bootstrap_commands:
            result = computer.bash(cmd)

        # Clone the repository with PAT
        clone_url = input.git_remote.replace("https://", f"https://{input.git_token}@")
        computer.bash(f"git clone {clone_url} ~/workspace")
        computer.bash(f"cd ~/workspace && git checkout {input.branch}")

        # Create tasks.md if it doesn't exist
        computer.bash("touch ~/workspace/tasks.md")

        # Upload Ralph Wiggum agent
        if RALPH_WIGGUM_PATH.exists():
            with open(RALPH_WIGGUM_PATH, "r") as f:
                ralph_code = f.read()
            # Use printf to handle special characters better
            computer.bash(f"cat > ~/ralph_wiggum.py << 'RALPH_EOF'\n{ralph_code}\nRALPH_EOF")

        # Set up environment variables
        env_content = f"""ANTHROPIC_API_KEY={input.anthropic_api_key}
ORGO_API_KEY={os.environ.get('ORGO_API_KEY', '')}
ORGO_COMPUTER_ID={vm_id}
WORKSPACE_PATH=/root/workspace
"""
        computer.bash(f"cat > ~/.env << 'ENV_EOF'\n{env_content}\nENV_EOF")

        # Create run script
        run_script = """#!/bin/bash
source ~/agent_env/bin/activate
export $(cat ~/.env | grep -v '^#' | xargs)
cd ~
python3 ~/ralph_wiggum.py >> ~/logs/ralph.log 2>&1
"""
        computer.bash(f"cat > ~/run_ralph.sh << 'RUN_EOF'\n{run_script}\nRUN_EOF")
        computer.bash("chmod +x ~/run_ralph.sh")

        # Start Xvfb for headless browser
        computer.bash("Xvfb :99 -screen 0 1920x1080x24 &")
        computer.bash("export DISPLAY=:99")

        # Start Ralph Wiggum in background
        computer.bash("nohup ~/run_ralph.sh &")

        # Update workspace status
        state.update_workspace_status(input.name, "ready", vm_url)

        return [
            TextContent(
                type="text",
                text=f"""Workspace '{input.name}' registered successfully!

VM ID: {vm_id}
URL: {vm_url}
Status: ready

The Ralph Wiggum agent is now running and polling for tasks.
Use plan_submit to send a task to this workspace.""",
            )
        ]

    except ImportError:
        return [
            TextContent(
                type="text",
                text="Error: 'orgo' package not installed. Run: pip install orgo",
            )
        ]
    except Exception as e:
        # Update status to error
        state.update_workspace_status(input.name, "error")
        return [TextContent(type="text", text=f"Error registering workspace: {str(e)}")]


async def handle_plan_submit(input: PlanSubmitInput) -> list[TextContent]:
    """Handle plan_submit tool call."""
    state = get_state()

    # Get workspace
    workspace = state.get_workspace(input.workspace_id)
    if not workspace:
        return [TextContent(type="text", text=f"Workspace '{input.workspace_id}' not found")]

    if workspace.status != "ready":
        return [
            TextContent(
                type="text",
                text=f"Workspace '{input.workspace_id}' is not ready (status: {workspace.status})",
            )
        ]

    try:
        from orgo import Computer

        computer = Computer(computer_id=workspace.vm_id)

        # Generate branch name if not provided
        branch_name = input.branch_name or f"agent/{uuid4().hex[:8]}"

        # Create and checkout branch
        computer.bash(f"cd ~/workspace && git checkout -b {branch_name}")

        # Escape plan text for bash
        escaped_plan = input.plan.replace("'", "'\\''")

        # Append task to tasks.md
        computer.bash(f"echo '- [ ] {escaped_plan}' >> ~/workspace/tasks.md")

        # Create plan record
        plan = Plan(
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            vm_id=workspace.vm_id,
            plan=input.plan,
            branch=branch_name,
            status="queued",
        )
        state.save_plan(plan)

        return [
            TextContent(
                type="text",
                text=f"""Plan submitted successfully!

Plan ID: {plan.id}
Workspace: {workspace.name}
Branch: {branch_name}
Status: queued

The Ralph Wiggum agent will pick up this task shortly.
Use plan_status(plan_id="{plan.id}") to check progress.""",
            )
        ]

    except ImportError:
        return [
            TextContent(
                type="text",
                text="Error: 'orgo' package not installed. Run: pip install orgo",
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error submitting plan: {str(e)}")]


async def handle_plan_status(input: PlanStatusInput) -> list[TextContent]:
    """Handle plan_status tool call."""
    state = get_state()

    # Get plan
    plan = state.get_plan(input.plan_id)
    if not plan:
        return [TextContent(type="text", text=f"Plan '{input.plan_id}' not found")]

    try:
        from orgo import Computer

        computer = Computer(computer_id=plan.vm_id)

        # Get recent logs
        log_result = computer.bash("tail -30 ~/logs/ralph.log 2>/dev/null || echo 'No logs yet'")

        # Get recent commits on the branch
        commits_result = computer.bash(
            f"cd ~/workspace && git log --oneline {plan.branch} -5 2>/dev/null || echo 'No commits yet'"
        )

        # Check if task is marked complete
        tasks_result = computer.bash("cat ~/workspace/tasks.md 2>/dev/null || echo ''")

        # Determine status from tasks.md
        current_status = plan.status
        if "[x]" in tasks_result and plan.plan[:30] in tasks_result:
            current_status = "completed"
            state.update_plan_status(plan.id, "completed")
        elif "[ ]" in tasks_result and plan.plan[:30] in tasks_result:
            current_status = "running"
            state.update_plan_status(plan.id, "running")

        return [
            TextContent(
                type="text",
                text=f"""Plan Status: {current_status}

Plan ID: {plan.id}
Workspace: {plan.workspace_name}
Branch: {plan.branch}
Created: {plan.created_at}

Task: {plan.plan}

Recent Agent Logs:
{log_result}

Recent Commits:
{commits_result}""",
            )
        ]

    except ImportError:
        return [
            TextContent(
                type="text",
                text="Error: 'orgo' package not installed. Run: pip install orgo",
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error checking status: {str(e)}")]


async def handle_workspace_sync(input: WorkspaceSyncInput) -> list[TextContent]:
    """Handle workspace_sync tool call."""
    state = get_state()

    # Get workspace
    workspace = state.get_workspace(input.workspace_id)
    if not workspace:
        return [TextContent(type="text", text=f"Workspace '{input.workspace_id}' not found")]

    try:
        from orgo import Computer

        computer = Computer(computer_id=workspace.vm_id)

        # Fetch from remote
        computer.bash("cd ~/workspace && git fetch origin")

        # Get all branches
        branches_result = computer.bash(
            "cd ~/workspace && git branch -a | grep -E 'agent/|origin/agent/' || echo 'No agent branches'"
        )

        # Get status of current branch
        status_result = computer.bash("cd ~/workspace && git status --short")

        # Get recent commits on agent branches
        commits = []
        branches = [b.strip() for b in branches_result.split("\n") if "agent/" in b]
        for branch in branches[:5]:  # Limit to 5 branches
            branch_name = branch.replace("* ", "").replace("remotes/origin/", "").strip()
            if branch_name:
                log = computer.bash(f"cd ~/workspace && git log --oneline {branch_name} -3 2>/dev/null || echo ''")
                if log.strip():
                    commits.append(f"\n{branch_name}:\n{log}")

        return [
            TextContent(
                type="text",
                text=f"""Workspace Sync Status

Workspace: {workspace.name}
Remote: {workspace.git_remote}
VM ID: {workspace.vm_id}

Agent Branches:
{branches_result}

Recent Commits:{chr(10).join(commits) if commits else ' None'}

Working Directory Status:
{status_result or '(clean)'}

To merge agent changes locally:
  git fetch origin
  git merge origin/<branch-name>

Or cherry-pick specific commits:
  git cherry-pick <commit-hash>""",
            )
        ]

    except ImportError:
        return [
            TextContent(
                type="text",
                text="Error: 'orgo' package not installed. Run: pip install orgo",
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error syncing workspace: {str(e)}")]


def main():
    """Run the MCP server."""
    import asyncio

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
