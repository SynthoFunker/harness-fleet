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
    },
    {
        "name": "get_fleet_status",
        "description": "Retrieve complete structured status of all running and ambient harnesses in the fleet, including resource usage, uptime, models, and installed harnesses.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_all": {
                    "type": "boolean",
                    "description": "Include historical/completed sessions if true (default: false).",
                    "default": False
                }
            },
            "required": []
        }
    },
    {
        "name": "spawn_harness",
        "description": "Spawn a new AI agent harness session (e.g. codex, opencode, hermes, pi, freebuff) inside an isolated Git worktree.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "harness": {
                    "type": "string",
                    "description": "The harness to spawn (codex, opencode, hermes, pi, freebuff, antigravity)."
                },
                "task_name": {
                    "type": "string",
                    "description": "Short identifier/slug for the task and branch name."
                },
                "prompt": {
                    "type": "string",
                    "description": "Initial instructions or goal for the agent."
                },
                "model": {
                    "type": "string",
                    "description": "Optional specific AI model override."
                },
                "use_worktree": {
                    "type": "boolean",
                    "description": "Whether to isolate the agent in a dedicated Git worktree branch (default: true).",
                    "default": True
                }
            },
            "required": ["harness", "task_name", "prompt"]
        }
    },
    {
        "name": "handover_to_harness",
        "description": "Transfer session state out-of-band via session-transmuter and spawn or continue with a target harness agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_session": {
                    "type": "string",
                    "description": "Source harness name or session ID to transmute state from."
                },
                "to_harness": {
                    "type": "string",
                    "description": "Target harness to spawn (codex, opencode, hermes, pi, freebuff)."
                },
                "task": {
                    "type": "string",
                    "description": "Optional task name for the target worktree."
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional additional directive for the target agent."
                },
                "model": {
                    "type": "string",
                    "description": "Optional AI model override for target harness."
                }
            },
            "required": ["from_session", "to_harness"]
        }
    },
    {
        "name": "diff_session",
        "description": "Inspect the Git diff of code modifications produced by an agent session in its isolated worktree.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID whose branch worktree diff to inspect."
                }
            },
            "required": ["session_id"]
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

    elif tool_name == "get_fleet_status":
        show_all = bool(args.get("show_all", False))
        from hmux.registry import HarnessRegistry
        from hmux.dashboard import (
            get_fleet_sessions,
            format_uptime,
            format_resource,
            format_model_name,
            extract_model_from_cmdline,
        )

        reg = HarnessRegistry()
        active_sessions, remaining_externals, combined_sessions = get_fleet_sessions(
            router.state_mgr, reg, show_all=show_all
        )

        session_list = []
        for s in active_sessions:
            session_list.append({
                "session_id": s.session_id,
                "harness": s.harness,
                "model": s.model or reg.resolve_model(s.harness),
                "task_name": s.task_name,
                "worktree_path": s.worktree_path,
                "branch": s.branch,
                "pid": s.pid,
                "status": s.status,
                "is_alive": s.is_alive(),
                "start_time": s.start_time,
                "uptime": format_uptime(s.start_time),
                "resource_usage": format_resource(s.pid) if (s.status == "RUNNING" and s.is_alive()) else "-",
                "metadata": s.metadata,
            })

        externals_list = []
        for e in remaining_externals:
            externals_list.append({
                "pid": e.pid,
                "harness": e.harness,
                "model": format_model_name(extract_model_from_cmdline(e.cmdline) or reg.resolve_model(e.harness)),
                "cwd": e.cwd,
                "cpu_percent": e.cpu_percent,
                "memory_mb": e.memory_mb,
                "cmdline": e.cmdline,
            })

        result = {
            "active_count": len([s for s in combined_sessions if s.status == "RUNNING" and s.is_alive()]),
            "installed_harnesses": [h.key for h in reg.list_installed()],
            "sessions": session_list,
            "external_processes": externals_list,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    elif tool_name == "spawn_harness":
        harness = args.get("harness")
        task_name = args.get("task_name")
        prompt = args.get("prompt")
        model = args.get("model")
        use_worktree = args.get("use_worktree", True)

        if not harness or not task_name:
            return {"content": [{"type": "text", "text": "Missing required arguments: 'harness' and 'task_name'."}], "isError": True}

        from hmux.supervisor import Supervisor
        sup = Supervisor()
        session = sup.spawn(
            harness_name=harness,
            task_name=task_name,
            prompt=prompt,
            model=model,
            use_worktree=use_worktree,
        )
        res_data = {
            "session_id": session.session_id,
            "harness": session.harness,
            "task_name": session.task_name,
            "branch": session.branch,
            "worktree_path": session.worktree_path,
            "log_file": session.log_file,
            "status": session.status,
            "model": session.model,
        }
        return {"content": [{"type": "text", "text": json.dumps(res_data, indent=2)}]}

    elif tool_name == "handover_to_harness":
        from_session = args.get("from_session")
        to_harness = args.get("to_harness")
        task = args.get("task")
        prompt = args.get("prompt", "")
        model = args.get("model")

        if not from_session or not to_harness:
            return {"content": [{"type": "text", "text": "Missing required arguments: 'from_session' and 'to_harness'."}], "isError": True}

        from hmux.supervisor import Supervisor
        sup = Supervisor()

        existing = sup.state.get(from_session)
        if existing:
            source_harness = existing.harness
            source_session_id = existing.session_id
        else:
            h_info = sup.registry.get(from_session)
            if h_info:
                source_harness = h_info.key
                source_session_id = None
            else:
                source_session_id = from_session
                source_harness = from_session

        session = sup.spawn(
            harness_name=to_harness,
            task_name=task or f"handover-{to_harness}",
            prompt=prompt,
            from_harness=source_harness,
            from_session=source_session_id,
            use_worktree=True,
            model=model,
        )
        res_data = {
            "session_id": session.session_id,
            "harness": session.harness,
            "from_harness": source_harness,
            "from_session": source_session_id,
            "task_name": session.task_name,
            "branch": session.branch,
            "worktree_path": session.worktree_path,
            "status": session.status,
            "model": session.model,
        }
        return {"content": [{"type": "text", "text": json.dumps(res_data, indent=2)}]}

    elif tool_name == "diff_session":
        session_id = args.get("session_id")
        if not session_id:
            return {"content": [{"type": "text", "text": "Missing required argument: 'session_id'."}], "isError": True}

        session = router.state_mgr.get(session_id)
        if not session:
            return {"content": [{"type": "text", "text": f"Session '{session_id}' not found."}], "isError": True}

        if not session.branch:
            return {"content": [{"type": "text", "text": f"Session '{session_id}' did not use an isolated git branch."}]}

        from hmux.worktree import WorktreeManager
        wt_mgr = WorktreeManager(session.worktree_path or os.getcwd())
        diff_text = wt_mgr.get_diff(session.branch)
        if not diff_text.strip():
            diff_text = f"No changes made yet on branch {session.branch}."
        return {"content": [{"type": "text", "text": diff_text}]}

    else:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
            "isError": True
        }


def export_antigravity_schemas(target_dir: str) -> List[str]:
    """Export tool schemas in Antigravity lazy tool JSON format to target directory."""
    os.makedirs(target_dir, exist_ok=True)
    exported = []
    for tool in TOOLS:
        schema = {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool.get("inputSchema", {})
        }
        path = os.path.join(target_dir, f"{tool['name']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2)
        exported.append(path)
    return exported


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
