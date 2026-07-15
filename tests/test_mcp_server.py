"""Tests for sao.provenance.mcp_server — stdio MCP server (JSON-RPC 2.0)."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import mcp_server

pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")


def handle(message, repo_path=Path(".")):
    return mcp_server.handle_message(message, repo_path)


def req(msg_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def tool_payload(response):
    """Decode the JSON string inside a tools/call result."""
    content = response["result"]["content"]
    assert content[0]["type"] == "text"
    return json.loads(content[0]["text"])


# ── Protocol unit tests ───────────────────────────────────────────────────────

class TestProtocol:
    def test_initialize(self):
        response = handle(req(1, "initialize", {"protocolVersion": "2025-06-18"}))
        result = response["result"]
        assert response["jsonrpc"] == "2.0" and response["id"] == 1
        assert result["protocolVersion"] == "2025-06-18"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "sao-provenance"

    def test_initialized_notification_has_no_response(self):
        message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert handle(message) is None

    def test_ping(self):
        assert handle(req(7, "ping"))["result"] == {}

    def test_tools_list(self):
        response = handle(req(2, "tools/list"))
        names = [t["name"] for t in response["result"]["tools"]]
        assert names == [
            "file_flight_plan", "list_missions", "get_mission",
            "verify_mission", "ledger_root", "blame_file",
        ]
        for tool in response["result"]["tools"]:
            assert tool["description"]
            assert tool["inputSchema"]["type"] == "object"

    def test_unknown_method_is_32601(self):
        response = handle(req(3, "resources/list"))
        assert response["error"]["code"] == -32601

    def test_unknown_notification_is_ignored(self):
        assert handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None

    def test_unknown_tool_is_invalid_params(self):
        response = handle(req(4, "tools/call", {"name": "nope", "arguments": {}}))
        assert response["error"]["code"] == -32602

    def test_tool_runtime_error_is_tool_error_result(self, git_repo):
        response = handle(
            req(5, "tools/call",
                {"name": "get_mission", "arguments": {"mission_id": "missing"}}),
            repo_path=git_repo,
        )
        assert response["result"]["isError"] is True
        assert "Mission not found" in tool_payload(response)["error"]

    def test_serve_reports_parse_error(self, git_repo):
        stdin = io.StringIO("this is not json\n")
        stdout = io.StringIO()
        mcp_server.serve(repo_path=git_repo, stdin=stdin, stdout=stdout)
        response = json.loads(stdout.getvalue().splitlines()[0])
        assert response["error"]["code"] == -32700

    def test_serve_skips_blank_lines(self, git_repo):
        stdin = io.StringIO("\n\n" + json.dumps(req(1, "ping")) + "\n")
        stdout = io.StringIO()
        mcp_server.serve(repo_path=git_repo, stdin=stdin, stdout=stdout)
        lines = stdout.getvalue().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == 1


# ── Tool behaviour (in-process) ───────────────────────────────────────────────

class TestTools:
    def test_ledger_root(self, provenance_repo):
        response = handle(
            req(1, "tools/call", {"name": "ledger_root", "arguments": {}}),
            repo_path=provenance_repo["repo"],
        )
        payload = tool_payload(response)
        assert payload["tree_size"] == 2
        assert len(payload["root_hash"]) == 64

    def test_list_missions(self, provenance_repo):
        response = handle(
            req(1, "tools/call", {"name": "list_missions", "arguments": {}}),
            repo_path=provenance_repo["repo"],
        )
        missions = tool_payload(response)["missions"]
        ids = [m["mission_id"] for m in missions]
        assert provenance_repo["mission_a"]["mission_id"] in ids
        assert provenance_repo["mission_b"]["mission_id"] in ids

    def test_get_mission(self, provenance_repo):
        mission_id = provenance_repo["mission_a"]["mission_id"]
        response = handle(
            req(1, "tools/call",
                {"name": "get_mission", "arguments": {"mission_id": mission_id}}),
            repo_path=provenance_repo["repo"],
        )
        payload = tool_payload(response)
        assert payload["manifest"]["mission_id"] == mission_id
        assert payload["attestation"]["version"] == "sao-attestation/2"

    def test_verify_mission(self, provenance_repo):
        mission_id = provenance_repo["mission_b"]["mission_id"]
        response = handle(
            req(1, "tools/call",
                {"name": "verify_mission", "arguments": {"mission_id": mission_id}}),
            repo_path=provenance_repo["repo"],
        )
        payload = tool_payload(response)
        assert payload["seal"]["verified"] is True
        assert payload["ledger"]["in_ledger"] is True
        assert payload["ledger"]["inclusion_ok"] is True
        assert payload["verified"] is True

    def test_blame_file(self, provenance_repo):
        response = handle(
            req(1, "tools/call",
                {"name": "blame_file", "arguments": {"path": "src/alpha.py"}}),
            repo_path=provenance_repo["repo"],
        )
        payload = tool_payload(response)
        assert all(
            l["mission_id"] == provenance_repo["mission_a"]["mission_id"]
            for l in payload["lines"]
        )

    def test_file_flight_plan(self, git_repo):
        response = handle(
            req(1, "tools/call", {
                "name": "file_flight_plan",
                "arguments": {
                    "name": "mcp plan", "intent": "test", "scope": ["src/*"],
                },
            }),
            repo_path=git_repo,
        )
        payload = tool_payload(response)
        assert payload["filed"] is True
        assert (git_repo / "blackbox" / "flightplan.pending.json").exists()
        assert payload["plan"]["scope"] == ["src/*"]


# ── End-to-end over subprocess stdin/stdout ───────────────────────────────────

class TestEndToEnd:
    def test_initialize_list_call_over_stdio(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]

        requests = [
            req(1, "initialize", {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            }),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            req(2, "tools/list"),
            req(3, "tools/call", {
                "name": "verify_mission",
                "arguments": {"mission_id": mission_id},
            }),
            req(4, "tools/call", {
                "name": "file_flight_plan",
                "arguments": {
                    "name": "e2e", "intent": "stdio test", "scope": ["src/*"],
                },
            }),
        ]
        stdin_text = "".join(json.dumps(r) + "\n" for r in requests)

        proc = subprocess.run(
            [sys.executable, "-m", "sao.cli", "mcp"],
            cwd=repo, input=stdin_text,
            capture_output=True, text=True, encoding="utf-8",
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

        responses = {}
        for line in proc.stdout.splitlines():
            message = json.loads(line)
            responses[message["id"]] = message

        # One response per request; the notification produced none.
        assert set(responses) == {1, 2, 3, 4}
        assert responses[1]["result"]["protocolVersion"] == "2025-06-18"
        assert responses[1]["result"]["serverInfo"]["name"] == "sao-provenance"

        tool_names = [t["name"] for t in responses[2]["result"]["tools"]]
        assert "verify_mission" in tool_names and "blame_file" in tool_names

        verify_payload = tool_payload(responses[3])
        assert verify_payload["verified"] is True

        plan_payload = tool_payload(responses[4])
        assert plan_payload["filed"] is True
        assert (repo / "blackbox" / "flightplan.pending.json").exists()
