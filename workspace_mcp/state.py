"""
State management for workspace and plan tracking.
Uses JSON file storage for MVP - can be upgraded to a database later.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class Workspace(BaseModel):
    """A registered workspace (git repo mapped to a VM)."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str
    vm_id: str
    git_remote: str
    branch: str = "main"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "bootstrapping"  # bootstrapping | ready | error
    url: Optional[str] = None


class Plan(BaseModel):
    """A submitted plan for execution."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    workspace_id: str
    workspace_name: str
    vm_id: str
    plan: str
    branch: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "queued"  # queued | running | completed | failed
    error: Optional[str] = None


class StateStore:
    """JSON-based state persistence for MVP."""

    def __init__(self, state_dir: Optional[str] = None):
        if state_dir:
            self.state_dir = Path(state_dir)
        else:
            # Default to ~/.workspace-mcp/
            self.state_dir = Path.home() / ".workspace-mcp"

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_file = self.state_dir / "workspaces.json"
        self.plans_file = self.state_dir / "plans.json"

        # Initialize files if they don't exist
        if not self.workspaces_file.exists():
            self._write_json(self.workspaces_file, {})
        if not self.plans_file.exists():
            self._write_json(self.plans_file, {})

    def _read_json(self, path: Path) -> dict:
        with open(path, "r") as f:
            return json.load(f)

    def _write_json(self, path: Path, data: dict) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    # Workspace operations

    def save_workspace(self, workspace: Workspace) -> Workspace:
        """Save a workspace, keyed by name."""
        workspaces = self._read_json(self.workspaces_file)
        workspaces[workspace.name] = workspace.model_dump()
        self._write_json(self.workspaces_file, workspaces)
        return workspace

    def get_workspace(self, name: str) -> Optional[Workspace]:
        """Get a workspace by name."""
        workspaces = self._read_json(self.workspaces_file)
        if name in workspaces:
            return Workspace(**workspaces[name])
        return None

    def update_workspace_status(self, name: str, status: str, url: Optional[str] = None) -> Optional[Workspace]:
        """Update workspace status."""
        workspace = self.get_workspace(name)
        if workspace:
            workspace.status = status
            if url:
                workspace.url = url
            return self.save_workspace(workspace)
        return None

    def list_workspaces(self) -> list[Workspace]:
        """List all workspaces."""
        workspaces = self._read_json(self.workspaces_file)
        return [Workspace(**w) for w in workspaces.values()]

    def delete_workspace(self, name: str) -> bool:
        """Delete a workspace by name."""
        workspaces = self._read_json(self.workspaces_file)
        if name in workspaces:
            del workspaces[name]
            self._write_json(self.workspaces_file, workspaces)
            return True
        return False

    # Plan operations

    def save_plan(self, plan: Plan) -> Plan:
        """Save a plan, keyed by ID."""
        plans = self._read_json(self.plans_file)
        plans[plan.id] = plan.model_dump()
        self._write_json(self.plans_file, plans)
        return plan

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Get a plan by ID."""
        plans = self._read_json(self.plans_file)
        if plan_id in plans:
            return Plan(**plans[plan_id])
        return None

    def update_plan_status(self, plan_id: str, status: str, error: Optional[str] = None) -> Optional[Plan]:
        """Update plan status."""
        plan = self.get_plan(plan_id)
        if plan:
            plan.status = status
            if error:
                plan.error = error
            return self.save_plan(plan)
        return None

    def list_plans(self, workspace_id: Optional[str] = None) -> list[Plan]:
        """List all plans, optionally filtered by workspace."""
        plans = self._read_json(self.plans_file)
        result = [Plan(**p) for p in plans.values()]
        if workspace_id:
            result = [p for p in result if p.workspace_id == workspace_id]
        return result

    def get_plans_by_workspace(self, workspace_name: str) -> list[Plan]:
        """Get all plans for a workspace by name."""
        plans = self._read_json(self.plans_file)
        return [Plan(**p) for p in plans.values() if p.workspace_name == workspace_name]


# Global state instance
_state: Optional[StateStore] = None


def get_state() -> StateStore:
    """Get or create the global state store."""
    global _state
    if _state is None:
        _state = StateStore()
    return _state
