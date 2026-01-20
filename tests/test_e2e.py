"""
End-to-end tests for the Workspace MCP Server.

These tests require:
- ORGO_API_KEY environment variable
- ANTHROPIC_API_KEY environment variable
- A test GitHub repository with a PAT

Run with: pytest tests/test_e2e.py -v
"""

import os
import time

import pytest

# Skip all tests if required environment variables are not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("ORGO_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ORGO_API_KEY and ANTHROPIC_API_KEY required for E2E tests",
)


class TestStateManagement:
    """Test the state management module without Orgo."""

    def test_workspace_crud(self, tmp_path):
        """Test workspace create, read, update, delete."""
        from workspace_mcp.state import StateStore, Workspace

        store = StateStore(state_dir=str(tmp_path))

        # Create
        workspace = Workspace(
            name="test-workspace",
            vm_id="vm-123",
            git_remote="https://github.com/test/repo.git",
            branch="main",
        )
        store.save_workspace(workspace)

        # Read
        retrieved = store.get_workspace("test-workspace")
        assert retrieved is not None
        assert retrieved.name == "test-workspace"
        assert retrieved.vm_id == "vm-123"

        # Update
        store.update_workspace_status("test-workspace", "ready", "https://example.com")
        updated = store.get_workspace("test-workspace")
        assert updated.status == "ready"
        assert updated.url == "https://example.com"

        # List
        workspaces = store.list_workspaces()
        assert len(workspaces) == 1

        # Delete
        store.delete_workspace("test-workspace")
        deleted = store.get_workspace("test-workspace")
        assert deleted is None

    def test_plan_crud(self, tmp_path):
        """Test plan create, read, update."""
        from workspace_mcp.state import Plan, StateStore

        store = StateStore(state_dir=str(tmp_path))

        # Create
        plan = Plan(
            workspace_id="ws-123",
            workspace_name="test-workspace",
            vm_id="vm-123",
            plan="Create a hello world file",
            branch="agent/test",
        )
        store.save_plan(plan)

        # Read
        retrieved = store.get_plan(plan.id)
        assert retrieved is not None
        assert retrieved.plan == "Create a hello world file"
        assert retrieved.status == "queued"

        # Update
        store.update_plan_status(plan.id, "completed")
        updated = store.get_plan(plan.id)
        assert updated.status == "completed"

        # List
        plans = store.list_plans()
        assert len(plans) == 1


class TestRalphWiggum:
    """Test Ralph Wiggum agent components."""

    def test_detect_workspace_type_nodejs(self, tmp_path):
        """Test detection of Node.js workspace."""
        # Import from agent module path
        import sys

        sys.path.insert(0, str(tmp_path.parent.parent / "agent"))

        # Create package.json
        (tmp_path / "package.json").write_text('{"name": "test"}')

        # Import and test
        from agent.ralph_wiggum import detect_workspace_type

        workspace_type = detect_workspace_type(str(tmp_path))
        assert workspace_type.type == "nodejs"
        assert "package.json" in workspace_type.detected_files

    def test_detect_workspace_type_python(self, tmp_path):
        """Test detection of Python workspace."""
        # Create pyproject.toml
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")

        from agent.ralph_wiggum import detect_workspace_type

        workspace_type = detect_workspace_type(str(tmp_path))
        assert workspace_type.type == "python"

    def test_detect_workspace_type_obsidian(self, tmp_path):
        """Test detection of Obsidian vault."""
        # Create .obsidian directory
        (tmp_path / ".obsidian").mkdir()

        from agent.ralph_wiggum import detect_workspace_type

        workspace_type = detect_workspace_type(str(tmp_path))
        assert workspace_type.type == "obsidian"

    def test_parse_tasks(self):
        """Test task parsing from tasks.md format."""
        from agent.ralph_wiggum import parse_tasks

        content = """# Tasks

- [ ] First uncompleted task
- [x] Completed task
- [ ] Second uncompleted task
- Not a task
"""
        tasks = parse_tasks(content)
        assert len(tasks) == 2
        assert tasks[0]["text"] == "First uncompleted task"
        assert tasks[1]["text"] == "Second uncompleted task"


@pytest.mark.integration
class TestE2EFlow:
    """Full end-to-end integration tests (require Orgo and Anthropic APIs)."""

    @pytest.fixture
    def test_config(self):
        """Get test configuration from environment."""
        return {
            "orgo_api_key": os.environ.get("ORGO_API_KEY"),
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY"),
            "git_token": os.environ.get("TEST_GIT_TOKEN"),
            "git_remote": os.environ.get("TEST_GIT_REMOTE"),
        }

    @pytest.mark.skipif(
        not os.environ.get("TEST_GIT_TOKEN") or not os.environ.get("TEST_GIT_REMOTE"),
        reason="TEST_GIT_TOKEN and TEST_GIT_REMOTE required",
    )
    async def test_full_workflow(self, test_config, tmp_path):
        """Test full workflow: register -> submit -> status -> sync."""
        from workspace_mcp.server import (
            handle_plan_status,
            handle_plan_submit,
            handle_workspace_register,
            handle_workspace_sync,
        )
        from workspace_mcp.state import StateStore

        # Use temp state directory
        import workspace_mcp.state

        workspace_mcp.state._state = StateStore(state_dir=str(tmp_path))

        # 1. Register workspace
        from workspace_mcp.server import WorkspaceRegisterInput

        register_result = await handle_workspace_register(
            WorkspaceRegisterInput(
                name="e2e-test",
                git_remote=test_config["git_remote"],
                git_token=test_config["git_token"],
                anthropic_api_key=test_config["anthropic_api_key"],
            )
        )
        assert "successfully" in register_result[0].text.lower()

        # 2. Submit a simple plan
        from workspace_mcp.server import PlanSubmitInput

        submit_result = await handle_plan_submit(
            PlanSubmitInput(
                workspace_id="e2e-test",
                plan="Create a file called hello.txt containing 'Hello World'",
            )
        )
        assert "submitted" in submit_result[0].text.lower()

        # Extract plan ID from result
        import re

        plan_id_match = re.search(r"Plan ID: (\w+)", submit_result[0].text)
        assert plan_id_match, "Could not find plan ID in result"
        plan_id = plan_id_match.group(1)

        # 3. Wait for execution (poll status)
        from workspace_mcp.server import PlanStatusInput

        max_wait = 120  # seconds
        poll_interval = 10
        elapsed = 0

        while elapsed < max_wait:
            status_result = await handle_plan_status(PlanStatusInput(plan_id=plan_id))
            if "completed" in status_result[0].text.lower():
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        assert "completed" in status_result[0].text.lower(), f"Task did not complete in {max_wait}s"

        # 4. Check sync status
        from workspace_mcp.server import WorkspaceSyncInput

        sync_result = await handle_workspace_sync(WorkspaceSyncInput(workspace_id="e2e-test"))
        assert "agent/" in sync_result[0].text.lower()

        print("\n=== E2E Test Passed ===")
        print(f"Plan ID: {plan_id}")
        print(sync_result[0].text)
