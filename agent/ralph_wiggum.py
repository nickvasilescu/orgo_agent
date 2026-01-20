#!/usr/bin/env python3
"""
Ralph Wiggum - Generalized AI Agent for Workspace Automation

This agent runs on Orgo VMs and executes natural language plans against
git-based workspaces. It supports various workspace types:
- Codebases (Node.js, Python, etc.)
- Obsidian vaults
- Document folders
- Any git repository

The agent polls ~/workspace/tasks.md for new tasks, executes them using
the Anthropic API with tool use, and commits results to git branches.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import anthropic
import httpx
from pydantic import BaseModel


# Configuration from environment
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ORGO_API_KEY = os.environ.get("ORGO_API_KEY")
ORGO_COMPUTER_ID = os.environ.get("ORGO_COMPUTER_ID")
WORKSPACE_PATH = os.environ.get("WORKSPACE_PATH", os.path.expanduser("~/workspace"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))  # seconds
MODEL = os.environ.get("MODEL", "claude-sonnet-4-20250514")


class WorkspaceType(BaseModel):
    """Detected workspace type and its specific tools."""
    type: str  # "nodejs" | "python" | "obsidian" | "generic"
    detected_files: list[str]
    available_commands: dict[str, str]  # command_name -> description


def detect_workspace_type(workspace_path: str) -> WorkspaceType:
    """Detect the type of workspace based on files present."""
    path = Path(workspace_path)
    detected_files = []
    commands = {}

    # Check for Node.js project
    if (path / "package.json").exists():
        detected_files.append("package.json")
        commands["npm_install"] = "npm install"
        commands["npm_test"] = "npm test"
        commands["npm_build"] = "npm run build"
        commands["npm_lint"] = "npm run lint"
        return WorkspaceType(type="nodejs", detected_files=detected_files, available_commands=commands)

    # Check for Python project
    if (path / "pyproject.toml").exists():
        detected_files.append("pyproject.toml")
        commands["pip_install"] = "pip install -e ."
        commands["pytest"] = "pytest"
        commands["ruff_check"] = "ruff check ."
        commands["ruff_format"] = "ruff format ."
        return WorkspaceType(type="python", detected_files=detected_files, available_commands=commands)

    if (path / "requirements.txt").exists():
        detected_files.append("requirements.txt")
        commands["pip_install"] = "pip install -r requirements.txt"
        commands["pytest"] = "pytest"
        return WorkspaceType(type="python", detected_files=detected_files, available_commands=commands)

    # Check for Obsidian vault
    if (path / ".obsidian").exists():
        detected_files.append(".obsidian/")
        commands["obsidian_search"] = "grep -r in markdown files"
        return WorkspaceType(type="obsidian", detected_files=detected_files, available_commands=commands)

    # Generic workspace
    return WorkspaceType(type="generic", detected_files=[], available_commands={
        "ls": "list files",
        "cat": "read files",
        "git_status": "git status"
    })


def log(message: str) -> None:
    """Log a message with timestamp."""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] {message}", flush=True)


def run_bash(command: str, cwd: Optional[str] = None) -> dict[str, Any]:
    """Run a bash command and return the result."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd or WORKSPACE_PATH
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Command timed out after 300 seconds",
            "return_code": -1,
            "success": False
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
            "success": False
        }


def read_file(file_path: str) -> dict[str, Any]:
    """Read a file from the workspace."""
    try:
        # Handle relative paths
        if not file_path.startswith("/"):
            file_path = os.path.join(WORKSPACE_PATH, file_path)

        with open(file_path, "r") as f:
            content = f.read()
        return {"content": content, "success": True}
    except Exception as e:
        return {"content": "", "error": str(e), "success": False}


def write_file(file_path: str, content: str) -> dict[str, Any]:
    """Write content to a file in the workspace."""
    try:
        # Handle relative paths
        if not file_path.startswith("/"):
            file_path = os.path.join(WORKSPACE_PATH, file_path)

        # Create parent directories if needed
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, "w") as f:
            f.write(content)
        return {"success": True, "path": file_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_files(directory: str = ".") -> dict[str, Any]:
    """List files in a directory."""
    try:
        if not directory.startswith("/"):
            directory = os.path.join(WORKSPACE_PATH, directory)

        files = []
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                files.append(f"{item}/")
            else:
                files.append(item)
        return {"files": sorted(files), "success": True}
    except Exception as e:
        return {"files": [], "error": str(e), "success": False}


def git_commit(message: str) -> dict[str, Any]:
    """Stage all changes and create a git commit."""
    # Stage all changes
    run_bash("git add -A", cwd=WORKSPACE_PATH)

    # Create commit
    escaped_message = message.replace('"', '\\"')
    result = run_bash(f'git commit -m "{escaped_message}"', cwd=WORKSPACE_PATH)
    return result


def git_push(branch: Optional[str] = None) -> dict[str, Any]:
    """Push commits to remote."""
    if branch:
        result = run_bash(f"git push -u origin {branch}", cwd=WORKSPACE_PATH)
    else:
        result = run_bash("git push", cwd=WORKSPACE_PATH)
    return result


def run_tests(workspace_type: WorkspaceType) -> dict[str, Any]:
    """Run the appropriate test command for the workspace type."""
    if workspace_type.type == "nodejs":
        return run_bash("npm test")
    elif workspace_type.type == "python":
        return run_bash("pytest -v")
    else:
        return {"success": False, "error": "No test framework detected"}


def run_build(workspace_type: WorkspaceType) -> dict[str, Any]:
    """Run the appropriate build command for the workspace type."""
    if workspace_type.type == "nodejs":
        return run_bash("npm run build")
    elif workspace_type.type == "python":
        return run_bash("pip install -e .")
    else:
        return {"success": False, "error": "No build system detected"}


def run_lint(workspace_type: WorkspaceType) -> dict[str, Any]:
    """Run the appropriate lint command for the workspace type."""
    if workspace_type.type == "nodejs":
        return run_bash("npm run lint")
    elif workspace_type.type == "python":
        return run_bash("ruff check . && ruff format --check .")
    else:
        return {"success": False, "error": "No linter detected"}


def search_files(pattern: str, file_pattern: str = "*") -> dict[str, Any]:
    """Search for a pattern in files."""
    result = run_bash(f'grep -r "{pattern}" --include="{file_pattern}" .', cwd=WORKSPACE_PATH)
    return result


def take_screenshot() -> dict[str, Any]:
    """Take a screenshot using Orgo API (if available)."""
    if not ORGO_API_KEY or not ORGO_COMPUTER_ID:
        return {"success": False, "error": "Orgo API not configured"}

    try:
        response = httpx.post(
            f"https://api.orgo.ai/v1/computers/{ORGO_COMPUTER_ID}/screenshot",
            headers={"Authorization": f"Bearer {ORGO_API_KEY}"},
            timeout=30
        )
        if response.status_code == 200:
            return {"success": True, "screenshot": "Screenshot taken"}
        else:
            return {"success": False, "error": f"API error: {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Define tools for Claude
TOOLS = [
    {
        "name": "bash",
        "description": "Run a bash command in the workspace. Use this for any shell operations, file manipulation, or system commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file. Paths are relative to the workspace root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to workspace)"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates parent directories if needed. Paths are relative to the workspace root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to workspace)"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_files",
        "description": "List files and directories. Directories are marked with a trailing slash.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory to list (relative to workspace, default: '.')"
                }
            }
        }
    },
    {
        "name": "search_files",
        "description": "Search for a pattern in files using grep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Pattern to search for"
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File pattern to search in (e.g., '*.py', '*.md')"
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "git_commit",
        "description": "Stage all changes and create a git commit with the given message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "git_push",
        "description": "Push commits to the remote repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Branch to push (optional, uses current branch if not specified)"
                }
            }
        }
    },
    {
        "name": "run_tests",
        "description": "Run the project's test suite (auto-detects framework: npm test, pytest, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "run_build",
        "description": "Build the project (auto-detects: npm run build, pip install, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "run_lint",
        "description": "Run linter/formatter (auto-detects: npm run lint, ruff, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "complete_task",
        "description": "Mark the current task as complete. Call this when you have finished the task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Summary of what was accomplished"
                }
            },
            "required": ["summary"]
        }
    }
]


def execute_tool(tool_name: str, tool_input: dict, workspace_type: WorkspaceType) -> str:
    """Execute a tool and return the result as a string."""
    log(f"Executing tool: {tool_name} with input: {json.dumps(tool_input)}")

    if tool_name == "bash":
        result = run_bash(tool_input["command"])
    elif tool_name == "read_file":
        result = read_file(tool_input["path"])
    elif tool_name == "write_file":
        result = write_file(tool_input["path"], tool_input["content"])
    elif tool_name == "list_files":
        result = list_files(tool_input.get("directory", "."))
    elif tool_name == "search_files":
        result = search_files(tool_input["pattern"], tool_input.get("file_pattern", "*"))
    elif tool_name == "git_commit":
        result = git_commit(tool_input["message"])
    elif tool_name == "git_push":
        result = git_push(tool_input.get("branch"))
    elif tool_name == "run_tests":
        result = run_tests(workspace_type)
    elif tool_name == "run_build":
        result = run_build(workspace_type)
    elif tool_name == "run_lint":
        result = run_lint(workspace_type)
    elif tool_name == "complete_task":
        result = {"success": True, "summary": tool_input["summary"]}
    else:
        result = {"error": f"Unknown tool: {tool_name}"}

    return json.dumps(result, indent=2)


def parse_tasks(tasks_content: str) -> list[dict[str, Any]]:
    """Parse tasks.md and return uncompleted tasks."""
    tasks = []
    lines = tasks_content.strip().split("\n")

    for line in lines:
        line = line.strip()
        # Match unchecked task: - [ ] task text
        match = re.match(r"^-\s*\[\s*\]\s*(.+)$", line)
        if match:
            tasks.append({
                "text": match.group(1).strip(),
                "line": line
            })

    return tasks


def mark_task_complete(task_line: str) -> None:
    """Mark a task as complete in tasks.md."""
    tasks_path = os.path.join(WORKSPACE_PATH, "tasks.md")

    try:
        with open(tasks_path, "r") as f:
            content = f.read()

        # Replace unchecked with checked
        new_line = task_line.replace("[ ]", "[x]")
        content = content.replace(task_line, new_line)

        with open(tasks_path, "w") as f:
            f.write(content)

        log(f"Marked task complete: {task_line[:50]}...")
    except Exception as e:
        log(f"Error marking task complete: {e}")


def execute_task(task: dict[str, Any], workspace_type: WorkspaceType, client: anthropic.Anthropic) -> bool:
    """Execute a single task using Claude with tool use."""
    task_text = task["text"]
    log(f"Starting task: {task_text}")

    # Build system prompt with workspace context
    system_prompt = f"""You are Ralph Wiggum, an AI agent executing tasks in a workspace.

WORKSPACE TYPE: {workspace_type.type}
WORKSPACE PATH: {WORKSPACE_PATH}
DETECTED FILES: {', '.join(workspace_type.detected_files) or 'none'}
AVAILABLE COMMANDS: {json.dumps(workspace_type.available_commands)}

Your job is to complete the given task by using the available tools.
Work step by step, reading files to understand the codebase before making changes.
Always commit your changes with meaningful commit messages.
When you're done, call the complete_task tool with a summary.

IMPORTANT:
- Be careful with destructive operations
- Test changes when possible before committing
- Write clear commit messages explaining what you changed
- If you encounter errors, try to fix them before giving up"""

    messages = [
        {"role": "user", "content": f"Please complete this task: {task_text}"}
    ]

    max_iterations = 50
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        log(f"Iteration {iteration}/{max_iterations}")

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=messages
            )
        except Exception as e:
            log(f"API error: {e}")
            return False

        # Process response
        assistant_message = {"role": "assistant", "content": response.content}
        messages.append(assistant_message)

        # Check if we're done
        if response.stop_reason == "end_turn":
            log("Task completed (no more tool calls)")
            mark_task_complete(task["line"])
            return True

        # Process tool calls
        tool_results = []
        task_completed = False

        for block in response.content:
            if block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                # Check for task completion
                if tool_name == "complete_task":
                    log(f"Task completed: {tool_input.get('summary', 'No summary')}")
                    mark_task_complete(task["line"])
                    task_completed = True
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({"success": True, "message": "Task marked as complete"})
                    })
                else:
                    result = execute_tool(tool_name, tool_input, workspace_type)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

        if task_completed:
            return True

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    log(f"Task exceeded max iterations ({max_iterations})")
    return False


def main() -> None:
    """Main agent loop - poll for tasks and execute them."""
    log("Ralph Wiggum starting up...")

    # Validate configuration
    if not ANTHROPIC_API_KEY:
        log("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    # Initialize Anthropic client
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Detect workspace type
    workspace_type = detect_workspace_type(WORKSPACE_PATH)
    log(f"Detected workspace type: {workspace_type.type}")
    log(f"Detected files: {workspace_type.detected_files}")

    tasks_path = os.path.join(WORKSPACE_PATH, "tasks.md")

    log(f"Polling {tasks_path} every {POLL_INTERVAL}s")
    log("Ready for tasks!")

    while True:
        try:
            # Read tasks.md
            if os.path.exists(tasks_path):
                with open(tasks_path, "r") as f:
                    tasks_content = f.read()

                # Parse and get uncompleted tasks
                tasks = parse_tasks(tasks_content)

                if tasks:
                    log(f"Found {len(tasks)} pending task(s)")

                    # Execute first pending task
                    task = tasks[0]
                    success = execute_task(task, workspace_type, client)

                    if success:
                        log("Task completed successfully")
                        # Push changes after each task
                        git_push()
                    else:
                        log("Task failed")

            # Sleep before next poll
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log("Shutting down...")
            break
        except Exception as e:
            log(f"Error in main loop: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
