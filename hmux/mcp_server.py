"""
HMUX Model Context Protocol (MCP) Server
Exposes live inter-harness messaging tools to AI coding agents (Antigravity, Codex, Pi, etc.)
over standard JSON-RPC 2.0 stdio transport.
"""

import os
import sys
import json
import traceback
from typing import Dict, Any, List

from hmux.router import MessageRouter
from hmux.catalog import SessionCatalog

router = MessageRouter()
catalog = SessionCatalog()


def get_caller_session_id(params: Dict[str, Any]) -> str:
    """OpenCode attaches the invoking session ID to every MCP call's metadata."""
    meta = params.get("_meta") or {}
    return (
        meta.get("ai.opencode/sessionID")
        or meta.get("ai.opencode/sessionId")
        or ""
    )


def ensure_caller_session(caller_id: str) -> None:
    """
    Register the calling OpenCode session in the HMUX fleet so its identity is
    visible to peers and loopback replies can be routed back into it.
    """
    if not caller_id or not caller_id.startswith("ses_"):
        return
    try:
        if router.state_mgr.get(caller_id):
            return
        from hmux.opencode import OpenCodeDriver
        info = OpenCodeDriver().get_session_info(caller_id)
        if not info:
            return
        from hmux.state import FleetSession
        session = FleetSession(
            session_id=caller_id,
            harness="opencode",
            task_name=info["title"],
            prompt="",
            worktree_path=info["directory"] or os.getcwd(),
            pid=os.getppid(),
            status="RUNNING",
            metadata={
                "ambient": True,
                "native_session_id": caller_id,
                "mcp": True,
            },
            model=info["model"],
        )
        router.state_mgr.register(session)
    except Exception:
        # Registration is best effort; messaging must keep working without it.
        pass


TOOLS = [
    {
        "name": "list_active_harnesses",
        "description": "List all currently active AI coding agent sessions in the local fleet (e.g. Codex, Pi, OpenCode, Hermes) and their ongoing tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_all_sessions",
        "description": "Discover all past and current sessions across all harnesses (Antigravity, Codex, Pi, Hermes, Freebuff) including their real titles, goals, and updated timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "harness": {
                    "type": "string",
                    "description": "Optional filter by harness (antigravity, codex, pi, hermes, freebuff)."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of sessions to return (default: 20).",
                    "default": 20
                }
            },
            "required": []
        }
    },
    {
        "name": "inspect_session",
        "description": "Inspect a specific session from any harness to view its title, objective/goal, files touched, and last turn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "harness": {
                    "type": "string",
                    "description": "The harness owning the session (antigravity, codex, pi, etc.)."
                },
                "session_id": {
                    "type": "string",
                    "description": "The exact session ID or partial prefix to inspect."
                }
            },
            "required": ["harness", "session_id"]
        }
    },
    {
        "name": "talk_to_harness",
        "description": "Send a message or instruction to an existing active harness session without creating a new session. Can wait for the other agent to report back and return its response.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_session": {
                    "type": "string",
                    "description": "The exact session ID of the target harness (from list_active_harnesses)."
                },
                "message": {
                    "type": "string",
                    "description": "The question, instruction, or message to deliver into the target session."
                },
                "from_session": {
                    "type": "string",
                    "description": "Your session ID or harness name (defaults to the calling OpenCode session).",
                    "default": "opencode"
                },
                "wait_for_reply": {
                    "type": "boolean",
                    "description": "Whether to wait for the target harness to formulate and send back a reply.",
                    "default": True
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Maximum seconds to wait for a reply (default: 60).",
                    "default": 60
                }
            },
            "required": ["target_session", "message"]
        }
    },
    {
        "name": "broadcast_to_harnesses",
        "description": "Send a broadcast notification or announcement to all currently active harnesses in the fleet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The announcement to broadcast across all active agent terminal sessions."
                },
                "from_session": {
                    "type": "string",
                    "description": "Your session ID or harness name (defaults to the calling OpenCode session).",
                    "default": "opencode"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "read_harness_inbox",
        "description": "Inspect recent incoming messages and inter-agent communication logs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID to filter messages for."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of messages to return (default: 15).",
                    "default": 15
                }
            },
            "required": []
        }
    }
]

def handle_call_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = params.get("name")
    args = params.get("arguments", {})
    caller_id = get_caller_session_id(params)
    ensure_caller_session(caller_id)
    default_from = caller_id or "opencode"

    if tool_name == "list_active_harnesses":
        sessions = router.list_active_sessions(include_opencode=True)
        text = json.dumps(sessions, indent=2)
        return {"content": [{"type": "text", "text": text}]}

    elif tool_name == "list_all_sessions":
        h_filter = args.get("harness")
        limit = args.get("limit", 20)
        sessions = catalog.list_all_sessions(harness_filter=h_filter, limit=limit)
        text = json.dumps(sessions, indent=2)
        return {"content": [{"type": "text", "text": text}]}

    elif tool_name == "inspect_session":
        harness = args.get("harness")
        s_id = args.get("session_id")
        summary = catalog.inspect_session(harness=harness, session_id=s_id)
        if not summary:
            return {"content": [{"type": "text", "text": f"Session '{s_id}' not found for harness '{harness}'."}], "isError": True}
        text = json.dumps(summary, indent=2)
        return {"content": [{"type": "text", "text": text}]}

    elif tool_name == "talk_to_harness":
        target = args.get("target_session")
        msg = args.get("message")
        from_s = args.get("from_session") or default_from
        wait = args.get("wait_for_reply", True)
        timeout = float(args.get("timeout_seconds", 60))

        success, result_text = router.send(
            to_session=target,
            message=msg,
            from_session=from_s,
            wait=wait,
            timeout=timeout
        )
        return {
            "content": [{"type": "text", "text": result_text}],
            "isError": not success
        }

    elif tool_name == "broadcast_to_harnesses":
        msg = args.get("message")
        from_s = args.get("from_session") or default_from
        res = router.broadcast(message=msg, from_session=from_s)
        text = f"Broadcast delivered to {res['delivered_count']} active session(s): {', '.join(res['recipients'])}"
        return {"content": [{"type": "text", "text": text}]}

    elif tool_name == "read_harness_inbox":
        s_id = args.get("session_id")
        limit = args.get("limit", 15)
        inbox = router.get_inbox(session_id=s_id, limit=limit)
        text = json.dumps(inbox, indent=2)
        return {"content": [{"type": "text", "text": text}]}

    else:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
            "isError": True
        }


def run_mcp_server():
    """Run standard stdio MCP loop reading newline-delimited JSON-RPC."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "hmux-router",
                        "version": "1.0.0"
                    }
                }
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS
                }
            }
        elif method == "tools/call":
            try:
                tool_res = handle_call_tool(params)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": tool_res
                }
            except Exception as e:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32603,
                        "message": str(e),
                        "data": traceback.format_exc()
                    }
                }
        elif method == "ping":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {}
            }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    run_mcp_server()
