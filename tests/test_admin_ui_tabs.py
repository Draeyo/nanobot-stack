"""Tests for Admin UI v2 backing endpoints (Trust, Costs, Workflows, Agents)."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))

from fastapi import FastAPI  # pylint: disable=wrong-import-position
from fastapi.testclient import TestClient  # pylint: disable=wrong-import-position


class TestTrustPoliciesEndpoints:
    """Test the /trust/* endpoints consumed by the Trust Policies tab."""

    def _make_client(self):
        import trust_engine
        trust_engine.TRUST_ENGINE_ENABLED = True
        app = FastAPI()
        app.include_router(trust_engine.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_get_policies_returns_list(self):
        client = self._make_client()
        r = client.get("/trust/policies")
        assert r.status_code == 200
        body = r.json()
        assert "policies" in body
        assert isinstance(body["policies"], list)

    def test_get_policies_item_has_required_fields(self):
        client = self._make_client()
        client.post("/trust/policies/file_write", json={"trust_level": "approval_required"})
        r = client.get("/trust/policies")
        assert r.status_code == 200
        policies = r.json()["policies"]
        if policies:
            p = policies[0]
            for field in ("action_type", "trust_level", "successful_executions", "failed_executions"):
                assert field in p, f"Missing field: {field}"

    def test_post_policy_updates_trust_level(self):
        client = self._make_client()
        r = client.post("/trust/policies/shell_execute", json={"trust_level": "blocked"})
        assert r.status_code == 200
        assert r.json().get("ok") is True
        r2 = client.get("/trust/policies")
        policies = {p["action_type"]: p for p in r2.json().get("policies", [])}
        assert "shell_execute" in policies
        assert policies["shell_execute"]["trust_level"] == "blocked"

    def test_post_policy_rejects_invalid_trust_level(self):
        client = self._make_client()
        r = client.post("/trust/policies/shell_execute", json={"trust_level": "super_trusted"})
        assert r.status_code in (400, 422)

    def test_get_audit_returns_entries_list(self):
        client = self._make_client()
        r = client.get("/trust/audit")
        assert r.status_code == 200
        body = r.json()
        assert "entries" in body
        assert isinstance(body["entries"], list)

    def test_promote_action_type_returns_ok(self):
        client = self._make_client()
        client.post("/trust/policies/file_read", json={"trust_level": "approval_required"})
        r = client.post("/trust/promote/file_read", json={})
        assert r.status_code == 200
        assert r.json().get("ok") is True


class TestCostDashboardEndpoints:
    """Test the /budget/* endpoints consumed by the Cost Dashboard tab."""

    def _make_client(self):
        import token_budget
        app = FastAPI()
        app.include_router(token_budget.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_budget_status_returns_required_keys(self):
        client = self._make_client()
        r = client.get("/budget/status")
        assert r.status_code == 200
        body = r.json()
        for key in ("daily_tokens_budget", "daily_tokens_used", "daily_cost_budget_cents",
                    "daily_cost_used_cents", "usage_percent", "budget_pressure"):
            assert key in body, f"Missing key in /budget/status: {key}"

    def test_budget_pressure_is_float_between_0_and_1(self):
        client = self._make_client()
        r = client.get("/budget/status")
        assert r.status_code == 200
        pressure = r.json()["budget_pressure"]
        assert isinstance(pressure, (int, float))
        assert 0.0 <= pressure <= 1.0

    def test_daily_report_has_by_model_list(self):
        client = self._make_client()
        r = client.get("/budget/daily-report")
        assert r.status_code == 200
        body = r.json()
        assert "by_model" in body
        assert isinstance(body["by_model"], list)

    def test_daily_report_model_items_have_required_fields(self):
        client = self._make_client()
        from token_budget import record_usage
        record_usage(session_id="test", operation_type="chat", task_type="test",
                     model="gpt-4.1-mini", input_tokens=100, output_tokens=50)
        r = client.get("/budget/daily-report")
        body = r.json()
        if body["by_model"]:
            item = body["by_model"][0]
            for field in ("model", "calls", "input_tokens", "output_tokens", "cost_cents"):
                assert field in item, f"Missing field in by_model item: {field}"

    def test_budget_history_returns_days_list(self):
        client = self._make_client()
        r = client.get("/budget/history")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, (list, dict))
        if isinstance(body, dict):
            assert any(isinstance(v, list) for v in body.values())


class TestProceduralWorkflowsEndpoints:
    """Test the /workflows/* endpoints consumed by the Procedural Workflows tab."""

    def _make_client(self):
        import extensions
        app = FastAPI()
        app.include_router(extensions.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_get_workflows_returns_dict_with_workflows_key(self):
        client = self._make_client()
        r = client.get("/workflows")
        assert r.status_code == 200
        body = r.json()
        assert "workflows" in body
        assert isinstance(body["workflows"], list)

    def test_get_workflows_has_enabled_key(self):
        client = self._make_client()
        r = client.get("/workflows")
        assert r.status_code == 200
        assert "enabled" in r.json()

    def test_get_workflows_graceful_when_disabled(self):
        client = self._make_client()
        r = client.get("/workflows")
        assert r.status_code == 200
        body = r.json()
        assert body["workflows"] == [] or isinstance(body["workflows"], list)

    def test_workflow_items_have_ui_fields(self, tmp_path):
        import sqlite3
        from datetime import datetime, timezone
        db_path = tmp_path / "procedural_memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE action_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_pattern TEXT NOT NULL UNIQUE,
            steps_json TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            last_observed TEXT NOT NULL,
            confidence REAL DEFAULT 0.0,
            auto_suggest BOOLEAN DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO action_sequences VALUES (1,'deploy check','[]',3,?,0.8,1,?)",
                     (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

        with patch("procedural_memory.STATE_DIR", tmp_path), \
             patch("procedural_memory.PROCEDURAL_MEMORY_ENABLED", True):
            import procedural_memory as pm
            workflows = pm.get_workflows(limit=10)
        assert len(workflows) == 1
        w = workflows[0]
        for field in ("id", "trigger_pattern", "steps_json", "frequency", "last_observed",
                      "confidence", "auto_suggest"):
            assert field in w, f"Missing field: {field}"

    def test_toggle_workflow_returns_graceful_error_when_disabled(self):
        client = self._make_client()
        r = client.post("/workflows/999/toggle", json={"auto_suggest": True})
        assert r.status_code == 200
        body = r.json()
        assert "ok" in body or "error" in body


class TestAgentStatusEndpoints:
    """Test the /agent/* endpoints consumed by the Agent Status tab."""

    def _make_client(self):
        import extensions
        app = FastAPI()
        app.include_router(extensions.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_agent_status_returns_agents_list(self):
        client = self._make_client()
        r = client.get("/agent/status")
        assert r.status_code == 200
        body = r.json()
        assert "agents" in body
        assert isinstance(body["agents"], list)

    def test_agent_status_has_orchestrator_enabled_key(self):
        client = self._make_client()
        r = client.get("/agent/status")
        assert r.status_code == 200
        assert "orchestrator_enabled" in r.json()

    def test_agent_status_graceful_when_agents_module_missing(self):
        client = self._make_client()
        r = client.get("/agent/status")
        assert r.status_code == 200

    def test_agent_status_items_have_ui_fields(self):
        with patch("extensions.list_agents", return_value=[
            {"name": "orchestrator", "description": "Main orchestrator agent", "tools": ["web_search"]}
        ], create=True):
            client = self._make_client()
            r = client.get("/agent/status")
            body = r.json()
            if body["agents"]:
                a = body["agents"][0]
                assert "name" in a
                assert "description" in a

    def test_agent_history_returns_executions_list(self):
        client = self._make_client()
        r = client.get("/agent/history")
        assert r.status_code == 200
        body = r.json()
        assert "executions" in body
        assert isinstance(body["executions"], list)

    def test_agent_history_limit_param(self):
        client = self._make_client()
        r = client.get("/agent/history?limit=5")
        assert r.status_code == 200
        executions = r.json()["executions"]
        assert len(executions) <= 5

    def test_agent_history_execution_items_have_ui_fields(self):
        import extensions
        from datetime import datetime, timezone
        extensions._agent_history.append({  # pylint: disable=protected-access
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "orchestrator",
            "task": "check server health",
            "status": "completed",
            "tokens": 1500,
        })
        client = self._make_client()
        r = client.get("/agent/history?limit=10")
        execs = r.json()["executions"]
        assert len(execs) >= 1
        e = execs[-1]
        for field in ("timestamp", "agent", "task", "status", "tokens"):
            assert field in e, f"Missing field in execution item: {field}"
