"""
mcp_server.py — dependency-free Model Context Protocol server over stdio.

``sao mcp`` speaks newline-delimited JSON-RPC 2.0 on stdin/stdout so a live
agent (Claude Code, etc.) can file flight plans and query provenance while
it works.  Implements the minimal MCP surface:

    initialize                  -> protocolVersion, capabilities.tools, serverInfo
    notifications/initialized   -> accepted (no response)
    ping                        -> {}
    tools/list                  -> the sao provenance tool catalogue
    tools/call                  -> dispatch to a tool
    anything else               -> JSON-RPC error -32601

Tools (each returns content: [{"type": "text", "text": <json-string>}]):

    file_flight_plan   name, intent, scope[]  -> writes the pending flight plan
    list_missions                             -> recorded missions
    get_mission        mission_id             -> manifest for one mission
    verify_mission     mission_id             -> seal + ledger inclusion result
    ledger_root                               -> current tree size + root hash
    blame_file         path                   -> line-level provenance mapping

Stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sao import __version__ as sao_version
from sao.blackbox import browser
from . import attest, blame as blame_mod, flightplan, ledger as ledger_mod

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "sao-provenance"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


# ── Tool catalogue ────────────────────────────────────────────────────────────

def _schema(properties: dict, required: list) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "file_flight_plan",
        "description": (
            "Pre-declare the scope of the next recorded mission. Writes "
            "blackbox/flightplan.pending.json; the next `sao run/wrap` "
            "consumes it into the sealed session."
        ),
        "inputSchema": _schema(
            {
                "name": {"type": "string", "description": "Mission name."},
                "intent": {"type": "string", "description": "What the mission will do."},
                "scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "fnmatch globs of repo-relative paths the mission may change.",
                },
            },
            ["name", "intent", "scope"],
        ),
    },
    {
        "name": "list_missions",
        "description": "List recorded missions (newest first).",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "get_mission",
        "description": "Return the full manifest of one recorded mission.",
        "inputSchema": _schema(
            {"mission_id": {"type": "string"}}, ["mission_id"]
        ),
    },
    {
        "name": "verify_mission",
        "description": (
            "Verify a mission's seal hashes and its inclusion in the "
            "transparency ledger."
        ),
        "inputSchema": _schema(
            {"mission_id": {"type": "string"}}, ["mission_id"]
        ),
    },
    {
        "name": "ledger_root",
        "description": "Current Merkle root and tree size of the transparency ledger.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "blame_file",
        "description": (
            "Line-level provenance for a file: map each line's commit to "
            "the agent mission that wrote it (via refs/notes/sao)."
        ),
        "inputSchema": _schema({"path": {"type": "string"}}, ["path"]),
    },
]


# ── Tool implementations (thin wrappers over the provenance modules) ─────────

def _tool_file_flight_plan(repo_path: Path, args: dict) -> dict:
    path = flightplan.file_flight_plan(
        repo_path,
        name=args["name"],
        intent=args["intent"],
        scope=list(args["scope"]),
    )
    return {
        "filed": True,
        "path": str(path),
        "plan": flightplan.load_pending(repo_path),
    }


def _tool_list_missions(repo_path: Path, args: dict) -> dict:
    sessions_root = browser.get_sessions_root(repo_path)
    return {"missions": browser.list_missions(sessions_root)}


def _find_session(repo_path: Path, mission_id: str) -> Path:
    sessions_root = browser.get_sessions_root(repo_path)
    return browser.find_mission(sessions_root, mission_id)


def _tool_get_mission(repo_path: Path, args: dict) -> dict:
    session_dir = _find_session(repo_path, args["mission_id"])
    manifest = browser.load_manifest(session_dir)
    statement, _ = attest.load_attestation(session_dir)
    return {"manifest": manifest, "attestation": statement}


def _tool_verify_mission(repo_path: Path, args: dict) -> dict:
    mission_id = args["mission_id"]
    session_dir = _find_session(repo_path, mission_id)
    seal_result = browser.verify_mission(session_dir)

    ledger = ledger_mod.Ledger(repo_path)
    entry = ledger.find(mission_id)
    if entry is None:
        ledger_result = {"in_ledger": False, "inclusion_ok": False}
    else:
        root_info = ledger.root()
        proof = ledger.inclusion_proof(entry["index"])
        inclusion_ok = ledger_mod.verify_inclusion(
            entry["leaf_hash"], entry["index"], proof,
            root_info["root_hash"], root_info["tree_size"],
        )
        ledger_result = {
            "in_ledger": True,
            "leaf_index": entry["index"],
            "leaf_hash": entry["leaf_hash"],
            "tree_size": root_info["tree_size"],
            "root": root_info["root_hash"],
            "inclusion_ok": inclusion_ok,
        }

    return {
        "mission_id": mission_id,
        "seal": {
            "manifest_ok": seal_result["manifest_ok"],
            "archive_ok": seal_result["archive_ok"],
            "session_directory_ok": seal_result["session_directory_ok"],
            "verified": seal_result["verified"],
        },
        "ledger": ledger_result,
        "verified": bool(
            seal_result["verified"] and ledger_result.get("inclusion_ok")
        ),
    }


def _tool_ledger_root(repo_path: Path, args: dict) -> dict:
    return ledger_mod.Ledger(repo_path).root()


def _tool_blame_file(repo_path: Path, args: dict) -> dict:
    return blame_mod.blame_file(repo_path, args["path"])


_TOOL_IMPLS = {
    "file_flight_plan": _tool_file_flight_plan,
    "list_missions": _tool_list_missions,
    "get_mission": _tool_get_mission,
    "verify_mission": _tool_verify_mission,
    "ledger_root": _tool_ledger_root,
    "blame_file": _tool_blame_file,
}


# ── JSON-RPC plumbing ─────────────────────────────────────────────────────────

def _result_message(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error_message(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_call_result(payload: dict, is_error: bool = False) -> dict:
    body = {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}]
    }
    if is_error:
        body["isError"] = True
    return body


def handle_message(message: dict, repo_path: Path):
    """Handle one JSON-RPC message.  Returns a response dict or None."""
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        return _result_message(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": sao_version},
        })

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _result_message(msg_id, {})

    if method == "tools/list":
        return _result_message(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        impl = _TOOL_IMPLS.get(tool_name)
        if impl is None:
            return _error_message(
                msg_id, JSONRPC_INVALID_PARAMS, f"Unknown tool: {tool_name}"
            )
        try:
            payload = impl(repo_path, arguments)
            return _result_message(msg_id, _tool_call_result(payload))
        except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
            return _result_message(
                msg_id,
                _tool_call_result({"error": str(exc)}, is_error=True),
            )

    if is_notification:
        return None  # unknown notifications are ignored per JSON-RPC
    return _error_message(
        msg_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}"
    )


def serve(repo_path=None, stdin=None, stdout=None) -> int:
    """Serve newline-delimited JSON-RPC 2.0 until EOF on stdin."""
    repo_path = Path(repo_path) if repo_path is not None else Path.cwd()
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error_message(None, JSONRPC_PARSE_ERROR, "Parse error")
        else:
            if not isinstance(message, dict):
                response = _error_message(
                    None, JSONRPC_INVALID_REQUEST, "Invalid request"
                )
            else:
                response = handle_message(message, repo_path)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
