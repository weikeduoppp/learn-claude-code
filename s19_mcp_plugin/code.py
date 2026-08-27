#!/usr/bin/env python3
"""
s19: MCP Tools — real/mock MCP transports + dynamic tool discovery.

Run:  python s19_mcp_plugin/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s18:
  - Real MCP transport adapters: mock, byted server PSM, stdio
  - normalize_mcp_name: normalize tool/server names
  - assemble_tool_pool: assembles builtin + MCP tools into one pool
  - connect_mcp: connect to an MCP transport, discover tools
  - Tool naming: mcp__{server}__{tool} with normalization
  - MCP tools carry internal annotations + permission checks
  - Teammates can use safe MCP tools from the shared pool
  - Connection failures retry with reconnect on demand
  - agent_loop uses dynamic tool pool (builtin + MCP), no prompt cache

ASCII flow:
  connect_mcp("docs") → MCPClient discovers tools →
  assemble_tool_pool → [builtin... , mcp__docs__search, mcp__docs__get_version]
  agent_loop uses assembled pool
"""
from __future__ import annotations


import os, subprocess, json, time, random, threading, re, asyncio, atexit, inspect, shlex
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field
from contextlib import AsyncExitStack
from typing import Any, Callable

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]


class AsyncRuntime:
    """Run async MCP clients behind the sync teaching loop."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def close(self):
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


ASYNC_RUNTIME = AsyncRuntime()


@dataclass
class ToolAnnotation:
    read_only: bool = False
    destructive: bool = False
    teammate_safe: bool = True
    requires_approval: bool = False
    notes: str = ""


@dataclass
class MCPConnectionConfig:
    transport: str = "mock"
    psm: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    connect_timeout: int = 10
    request_timeout: int = 600


def _json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "(no output)"
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=_json_default)
    except TypeError:
        return str(value)

# ── Task System ──

TASKS_DIR = WORKDIR / ".tasks"
TASKS_DIR.mkdir(exist_ok=True)


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
    worktree: str | None = None


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def create_task(subject: str, description: str = "",
                blockedBy: list[str] | None = None) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject, description=description,
        status="pending", owner=None,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def save_task(task: Task):
    _task_path(task.id).write_text(json.dumps(asdict(task), indent=2))


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))


def list_tasks() -> list[Task]:
    return [Task(**json.loads(p.read_text()))
            for p in sorted(TASKS_DIR.glob("task_*.json"))]


def get_task_json(task_id: str) -> str:
    return json.dumps(asdict(load_task(task_id)), indent=2)


def can_start(task_id: str) -> bool:
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status != "completed":
            return False
    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    if task.owner:
        return f"Task {task_id} already owned by {task.owner}"
    if not can_start(task_id):
        deps = [d for d in task.blockedBy
                if _task_path(d).exists() and load_task(d).status != "completed"]
        missing = [d for d in task.blockedBy if not _task_path(d).exists()]
        parts = []
        if deps: parts.append(f"blocked by: {deps}")
        if missing: parts.append(f"missing deps: {missing}")
        return "Cannot start — " + ", ".join(parts)
    task.owner = owner
    task.status = "in_progress"
    save_task(task)
    print(f"  \033[36m[claim] {task.subject} → in_progress\033[0m")
    return f"Claimed {task.id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete"
    task.status = "completed"
    save_task(task)
    unblocked = [t.subject for t in list_tasks()
                 if t.status == "pending" and t.blockedBy and can_start(t.id)]
    print(f"  \033[32m[complete] {task.subject} ✓\033[0m")
    msg = f"Completed {task.id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
    return msg


# ── Worktree System ──

WORKTREES_DIR = WORKDIR / ".worktrees"
WORKTREES_DIR.mkdir(exist_ok=True)

VALID_WT_NAME = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def validate_worktree_name(name: str) -> str | None:
    if not name:
        return "Worktree name cannot be empty"
    if name in (".", ".."):
        return f"'{name}' is not a valid worktree name"
    if not VALID_WT_NAME.match(name):
        return (f"Invalid worktree name '{name}': "
                "only letters, digits, dots, underscores, dashes (1-64 chars)")
    return None


def run_git(args: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(["git"] + args, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out[:5000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return False, "Error: git timeout"


def log_event(event_type: str, worktree_name: str, task_id: str = ""):
    event = {"type": event_type, "worktree": worktree_name,
             "task_id": task_id, "ts": time.time()}
    events_file = WORKTREES_DIR / "events.jsonl"
    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")


def create_worktree(name: str, task_id: str = "") -> str:
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    path = WORKTREES_DIR / name
    if path.exists():
        return f"Worktree '{name}' already exists at {path}"
    ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
    if not ok:
        return f"Git error: {result}"
    if task_id:
        bind_task_to_worktree(task_id, name)
    log_event("create", name, task_id)
    print(f"  \033[33m[worktree] created: {name} at {path}\033[0m")
    return f"Worktree '{name}' created at {path}"


def bind_task_to_worktree(task_id: str, worktree_name: str):
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    try:
        r1 = subprocess.run(["git", "status", "--porcelain"],
                            cwd=path, capture_output=True, text=True, timeout=10)
        files = len([l for l in r1.stdout.strip().splitlines() if l.strip()])
        r2 = subprocess.run(["git", "log", "@{push}..HEAD", "--oneline"],
                            cwd=path, capture_output=True, text=True, timeout=10)
        commits = len([l for l in r2.stdout.strip().splitlines() if l.strip()])
        return files, commits
    except Exception:
        return -1, -1


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    err = validate_worktree_name(name)
    if err:
        return err
    path = WORKTREES_DIR / name
    if not path.exists():
        return f"Worktree '{name}' not found"
    if not discard_changes:
        files, commits = _count_worktree_changes(path)
        if files < 0:
            return "Cannot verify status. Use discard_changes=true to force."
        if files > 0 or commits > 0:
            return (f"Worktree '{name}' has {files} file(s), {commits} commit(s). "
                    "Use discard_changes=true or keep_worktree.")
    ok1, _ = run_git(["worktree", "remove", str(path), "--force"])
    if not ok1:
        return f"Failed to remove worktree '{name}'"
    run_git(["branch", "-D", f"wt/{name}"])
    log_event("remove", name)
    print(f"  \033[33m[worktree] removed: {name}\033[0m")
    return f"Worktree '{name}' removed"


def keep_worktree(name: str) -> str:
    err = validate_worktree_name(name)
    if err:
        return err
    log_event("keep", name)
    return f"Worktree '{name}' kept for review (branch: wt/{name})"


# ── Prompt Assembly ──

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, "
             "create_task, list_tasks, get_task, claim_task, complete_task, "
             "spawn_teammate, send_message, check_inbox, "
             "request_shutdown, request_plan, review_plan, "
             "create_worktree, remove_worktree, keep_worktree, "
             "connect_mcp. MCP tools are prefixed mcp__{server}__{tool}. "
             "connect_mcp supports transport=mock|server|stdio. "
             "Teammates only receive MCP tools marked teammate_safe.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    sections = [PROMPT_SECTIONS["identity"],
                PROMPT_SECTIONS["tools"],
                PROMPT_SECTIONS["workspace"]]
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")
    teammate_names = list(active_teammates.keys())
    if teammate_names:
        sections.append(f"Active teammates: {', '.join(teammate_names)}")
    mcp_names = list(mcp_clients.keys())
    if mcp_names:
        sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")
    return "\n\n".join(sections)


DENY_LIST = ["rm -rf /", "sudo shutdown", "reboot", "mkfs", "dd if="]


def check_tool_permission(tool_name: str, tool_input: dict[str, Any],
                          actor: str) -> str | None:
    if tool_name == "bash":
        command = str(tool_input.get("command", ""))
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied: '{pattern}' is blocked"
    if tool_name.startswith("mcp__"):
        annotation = MCP_TOOL_ANNOTATIONS.get(tool_name, ToolAnnotation())
        if actor != "lead" and not annotation.teammate_safe:
            return f"Permission denied: {tool_name} is not available to teammates"
        if actor != "lead" and annotation.destructive:
            return f"Permission denied: destructive MCP tool '{tool_name}' is lead-only"
    return None


def dispatch_tool_use(block, handlers: dict[str, Callable], actor: str) -> str:
    denied = check_tool_permission(block.name, block.input, actor)
    if denied:
        return denied
    handler = handlers.get(block.name)
    if not handler:
        return "Unknown"
    try:
        return _format_value(handler(**block.input))
    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}"


# ── Basic Tools ──

def safe_path(p: str, cwd: Path = None) -> Path:
    base = cwd or WORKDIR
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str, cwd: Path = None) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=cwd or WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int | None = None, cwd: Path = None) -> str:
    try:
        lines = safe_path(path, cwd).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str, cwd: Path = None) -> str:
    try:
        fp = safe_path(path, cwd)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


# ── MessageBus ──

MAILBOX_DIR = WORKDIR / ".mailboxes"
MAILBOX_DIR.mkdir(exist_ok=True)


class MessageBus:
    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        msg = {"from": from_agent, "to": to_agent,
               "content": content, "type": msg_type,
               "ts": time.time(), "metadata": metadata or {}}
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a") as f:
            f.write(json.dumps(msg) + "\n")
        print(f"  \033[33m[bus] {from_agent} → {to_agent}: "
              f"({msg_type}) {content[:50]}\033[0m")

    def read_inbox(self, agent: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [json.loads(line) for line in inbox.read_text().splitlines()
                if line.strip()]
        inbox.unlink()
        return msgs


BUS = MessageBus()
active_teammates: dict[str, bool] = {}
DEFAULT_TEAMMATES = [
    {
        "name": "dog",
        "role": "default teammate",
        "prompt": (
            "You are the built-in teammate 'dog'. "
            "Stay available for delegated work, proactively claim unblocked tasks, "
            "and use teammate-safe MCP tools when they help. "
            "Send concise status updates to lead when you finish meaningful work."
        ),
        "idle_timeout": None,
    },
]

# ── Protocol State ──

@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default_factory=time.time)


pending_requests: dict[str, ProtocolState] = {}


def new_request_id() -> str:
    return f"req_{random.randint(0, 999999):06d}"


def match_response(response_type: str, request_id: str, approve: bool):
    state = pending_requests.get(request_id)
    if not state:
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        return
    state.status = "approved" if approve else "rejected"


def consume_lead_inbox(route_protocol=True) -> list[dict]:
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                match_response(msg_type, req_id, meta.get("approve", False))
    return msgs


# ── Autonomous Agent ──

IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60


def scan_unclaimed_tasks() -> list[dict]:
    unclaimed = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        task = json.loads(f.read_text())
        if (task.get("status") == "pending"
                and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed


def idle_poll(agent_name: str, messages: list,
              name: str, role: str,
              idle_timeout: int | None = IDLE_TIMEOUT) -> str:
    rounds = None if idle_timeout is None else idle_timeout // IDLE_POLL_INTERVAL
    while rounds is None or rounds > 0:
        time.sleep(IDLE_POLL_INTERVAL)
        if rounds is not None:
            rounds -= 1
        inbox = BUS.read_inbox(agent_name)
        if inbox:
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(name, "lead", "Shutting down.",
                             "shutdown_response",
                             {"request_id": req_id, "approve": True})
                    return "shutdown"
            messages.append({"role": "user",
                "content": "<inbox>" + json.dumps(inbox) + "</inbox>"})
            return "work"
        unclaimed = scan_unclaimed_tasks()
        if unclaimed:
            task_data = unclaimed[0]
            result = claim_task(task_data["id"], agent_name)
            if "Claimed" in result:
                wt_info = ""
                if task_data.get("worktree"):
                    wt_info = f"\nWork directory: {WORKTREES_DIR / task_data['worktree']}"
                messages.append({"role": "user",
                    "content": f"<auto-claimed>Task {task_data['id']}: "
                               f"{task_data['subject']}{wt_info}</auto-claimed>"})
                return "work"
    return "timeout"


# ── Teammate Thread ──

def spawn_teammate_thread(name: str, role: str, prompt: str,
                          idle_timeout: int | None = IDLE_TIMEOUT) -> str:
    if name in active_teammates:
        return f"Teammate '{name}' already exists"

    system = (f"You are '{name}', a {role}. "
              f"Use tools to complete tasks. "
              f"If a task has a worktree, work in that directory. "
              f"You may use teammate-safe MCP tools when available.")

    def handle_inbox_message(name: str, msg: dict, messages: list):
        msg_type = msg.get("type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")
        if msg_type == "shutdown_request":
            BUS.send(name, "lead", "Shutting down.",
                     "shutdown_response",
                     {"request_id": req_id, "approve": True})
            return True
        if msg_type == "plan_approval_response":
            approve = meta.get("approve", False)
            messages.append({"role": "user",
                "content": "[Plan approved]" if approve
                           else f"[Plan rejected] {msg['content']}"})
        return False

    def run():
        wt_ctx = {"path": None}

        def _wt_cwd():
            p = wt_ctx["path"]
            return Path(p) if p else None

        def _run_bash(command: str) -> str:
            return run_bash(command, cwd=_wt_cwd())

        def _run_read(path: str) -> str:
            return run_read(path, cwd=_wt_cwd())

        def _run_write(path: str, content: str) -> str:
            return run_write(path, content, cwd=_wt_cwd())

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks:
                return "No tasks."
            return "\n".join(
                f"  {t.id}: {t.subject} [{t.status}]"
                + (f" (wt:{t.worktree})" if t.worktree else "")
                for t in tasks)

        def _run_claim_task(task_id: str):
            result = claim_task(task_id, owner=name)
            if "Claimed" in result:
                task = load_task(task_id)
                wt_ctx["path"] = (str(WORKTREES_DIR / task.worktree)
                                  if task.worktree else None)
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            wt_ctx["path"] = None
            return result

        messages = [{"role": "user", "content": prompt}]
        teammate_tools = [
            {"name": "bash", "description": "Run a shell command.",
             "input_schema": {"type": "object",
                              "properties": {"command": {"type": "string"}},
                              "required": ["command"]}},
            {"name": "read_file", "description": "Read file.",
             "input_schema": {"type": "object",
                              "properties": {"path": {"type": "string"}},
                              "required": ["path"]}},
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object",
                              "properties": {"path": {"type": "string"},
                                             "content": {"type": "string"}},
                              "required": ["path", "content"]}},
            {"name": "send_message",
             "description": "Send message to another agent.",
             "input_schema": {"type": "object",
                              "properties": {"to": {"type": "string"},
                                             "content": {"type": "string"}},
                              "required": ["to", "content"]}},
            {"name": "submit_plan",
             "description": "Submit a plan for Lead approval.",
             "input_schema": {"type": "object",
                              "properties": {"plan": {"type": "string"}},
                              "required": ["plan"]}},
            {"name": "list_tasks",
             "description": "List all tasks.",
             "input_schema": {"type": "object", "properties": {},
                              "required": []}},
            {"name": "claim_task",
             "description": "Claim a pending task.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
            {"name": "complete_task",
             "description": "Mark an in-progress task as completed.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
        ]

        teammate_handlers = {
            "bash": _run_bash, "read_file": _run_read,
            "write_file": _run_write,
            "send_message": lambda to, content: (BUS.send(name, to, content),
                                                  "Sent")[1],
            "submit_plan": lambda plan: _teammate_submit_plan(name, plan),
            "list_tasks": _run_list_tasks,
            "claim_task": _run_claim_task,
            "complete_task": _run_complete_task,
        }

        while True:
            if len(messages) <= 3:
                messages.insert(0, {"role": "user",
                    "content": f"<identity>You are '{name}', role: {role}. "
                               f"Continue your work.</identity>"})
            should_shutdown = False
            for _ in range(10):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    stopped = handle_inbox_message(name, msg, messages)
                    if stopped:
                        should_shutdown = True
                        break
                if should_shutdown:
                    break
                if inbox and not should_shutdown:
                    non_protocol = [m for m in inbox
                                    if m.get("type") == "message"]
                    if non_protocol:
                        messages.append({"role": "user",
                            "content": "<inbox>" + json.dumps(non_protocol) + "</inbox>"})
                try:
                    dynamic_tools, dynamic_handlers = assemble_tool_pool(
                        base_tools=teammate_tools,
                        base_handlers=teammate_handlers,
                        audience="teammate",
                    )
                    response = client.messages.create(
                        model=MODEL, system=system, messages=messages[-20:],
                        tools=dynamic_tools, max_tokens=8000)
                except Exception:
                    break
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        output = dispatch_tool_use(block, dynamic_handlers, actor=name)
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": str(output)})
                messages.append({"role": "user", "content": results})
            if should_shutdown:
                break
            idle_result = idle_poll(name, messages, name, role, idle_timeout)
            if idle_result in ("shutdown", "timeout"):
                break

        summary = "Done."
        for msg in reversed(messages):
            if msg["role"] == "assistant" and isinstance(msg["content"], list):
                for b in msg["content"]:
                    if getattr(b, "type", None) == "text":
                        summary = b.text
                        break
                else:
                    continue
                break
        BUS.send(name, "lead", summary, "result")
        active_teammates.pop(name, None)

    active_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}"


def _teammate_submit_plan(from_name: str, plan: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="plan_approval",
        sender=from_name, target="lead",
        status="pending", payload=plan)
    BUS.send(from_name, "lead", plan,
             "plan_approval_request",
             {"request_id": req_id})
    return f"Plan submitted ({req_id})"


# ── Lead Protocol Tools ──

def run_request_shutdown(teammate: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="shutdown",
        sender="lead", target=teammate,
        status="pending", payload="")
    BUS.send("lead", teammate, "Shut down.", "shutdown_request",
             {"request_id": req_id})
    return f"Shutdown request sent to {teammate}"


def run_request_plan(teammate: str, task: str) -> str:
    BUS.send("lead", teammate, f"Submit plan for: {task}", "message")
    return f"Asked {teammate} to submit a plan"


def run_review_plan(request_id: str, approve: bool,
                    feedback: str = "") -> str:
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    state.status = "approved" if approve else "rejected"
    BUS.send("lead", state.sender,
             feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_response",
             {"request_id": request_id, "approve": approve})
    return f"Plan {'approved' if approve else 'rejected'}"


# ── MCP System (s19 new) ──

_DISALLOWED_CHARS = re.compile(r'[^a-zA-Z0-9_-]')
MCP_TOOL_ANNOTATIONS: dict[str, ToolAnnotation] = {}
MCP_TOOL_SOURCES: dict[str, str] = {}


def normalize_mcp_name(name: str) -> str:
    """Replace non [a-zA-Z0-9_-] with underscore."""
    return _DISALLOWED_CHARS.sub('_', name)


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        raw = dict(value.__dict__)
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    return {}


def _tool_field(tool_def: Any, key: str, default=None):
    if isinstance(tool_def, dict):
        return tool_def.get(key, default)
    return getattr(tool_def, key, default)


def _normalize_input_schema(tool_def: Any) -> dict[str, Any]:
    schema = (_tool_field(tool_def, "inputSchema")
              or _tool_field(tool_def, "input_schema")
              or {"type": "object", "properties": {}, "required": []})
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        dumped = schema.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(schema, "__dict__"):
        return dict(schema.__dict__)
    return {"type": "object", "properties": {}, "required": []}


def _infer_annotation(tool_name: str, description: str,
                      raw_annotations: dict[str, Any]) -> ToolAnnotation:
    raw = {str(k): v for k, v in raw_annotations.items()}
    lower = f"{tool_name} {description}".lower()
    read_only = bool(raw.get("readOnlyHint")) or any(
        token in lower for token in ("readonly", "read only", "search", "status", "get ", "list "))
    destructive = bool(raw.get("destructiveHint")) or any(
        token in lower for token in ("destructive", "trigger", "deploy", "delete", "remove", "shutdown"))
    requires_approval = bool(raw.get("requiresApproval")) or destructive
    teammate_safe = bool(raw.get("teammateSafe", read_only and not destructive))
    notes = str(raw.get("title") or raw.get("notes") or "").strip()
    return ToolAnnotation(
        read_only=read_only,
        destructive=destructive,
        teammate_safe=teammate_safe,
        requires_approval=requires_approval,
        notes=notes,
    )


def _describe_annotation(annotation: ToolAnnotation) -> str:
    tags = []
    if annotation.read_only:
        tags.append("read_only")
    if annotation.destructive:
        tags.append("destructive")
    if annotation.requires_approval:
        tags.append("approval")
    if annotation.teammate_safe:
        tags.append("teammate_safe")
    if not tags:
        return ""
    return f" [{', '.join(tags)}]"


def _normalize_tool_def(tool_def: Any) -> tuple[dict[str, Any], ToolAnnotation]:
    name = str(_tool_field(tool_def, "name", "unknown"))
    description = str(_tool_field(tool_def, "description", "") or "")
    raw_annotations = (_tool_field(tool_def, "annotations")
                       or _tool_field(tool_def, "annotation")
                       or {})
    annotation = _infer_annotation(name, description, _as_mapping(raw_annotations))
    return {
        "name": name,
        "description": description,
        "inputSchema": _normalize_input_schema(tool_def),
    }, annotation


def _format_mcp_result(result: Any) -> str:
    if result is None:
        return "(no output)"
    content = getattr(result, "content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            item_type = getattr(item, "type", None)
            if item_type == "text":
                parts.append(getattr(item, "text", ""))
                continue
            if item_type == "resource":
                resource = getattr(item, "resource", None)
                if resource and hasattr(resource, "text"):
                    parts.append(getattr(resource, "text", ""))
                    continue
            parts.append(_format_value(_as_mapping(item) or item))
        return "\n".join([p for p in parts if p]).strip() or "[No content]"
    if isinstance(result, (dict, list)):
        return _format_value(result)
    if hasattr(result, "model_dump"):
        return _format_value(result.model_dump())
    return str(result)


class MCPClient:
    """Sync facade for real or mock MCP connections."""

    def __init__(self, name: str, config: MCPConnectionConfig):
        self.name = name
        self.config = config
        self.tools: list[dict[str, Any]] = []
        self.annotations: dict[str, ToolAnnotation] = {}
        self.connected = False
        self.last_error = ""
        self._lock = threading.RLock()

    def connect(self) -> str:
        with self._lock:
            try:
                self._connect_impl()
            except Exception as e:
                self.connected = False
                self.last_error = str(e)
                return f"MCP connect failed for '{self.name}': {e}"
            self.connected = True
            self.last_error = ""
            tool_names = ", ".join(t["name"] for t in self.tools) or "(none)"
            return (f"Connected '{self.name}' via {self.config.transport}. "
                    f"Discovered {len(self.tools)} tools: {tool_names}")

    def reconnect(self) -> str:
        with self._lock:
            self.close()
            return self.connect()

    def close(self):
        try:
            self._close_impl()
        except Exception:
            pass
        self.connected = False

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        with self._lock:
            if not self.connected:
                reconnect_result = self.connect()
                if not self.connected:
                    return reconnect_result
            try:
                return self._call_tool_impl(tool_name, args)
            except Exception as first_error:
                self.connected = False
                self.last_error = str(first_error)
                reconnect_result = self.connect()
                if not self.connected:
                    return (f"MCP error: {first_error}\n"
                            f"Reconnect failed: {reconnect_result}")
                try:
                    return self._call_tool_impl(tool_name, args)
                except Exception as second_error:
                    self.last_error = str(second_error)
                    return f"MCP error after reconnect: {second_error}"

    def _connect_impl(self):
        raise NotImplementedError

    def _call_tool_impl(self, tool_name: str, args: dict[str, Any]) -> str:
        raise NotImplementedError

    def _close_impl(self):
        return None


class MockMCPClient(MCPClient):
    def __init__(self, name: str):
        super().__init__(name, MCPConnectionConfig(transport="mock"))
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, tool_defs: list[dict[str, Any]],
                 handlers: dict[str, Callable[..., Any]]):
        self.tools = []
        self.annotations = {}
        for tool_def in tool_defs:
            normalized, annotation = _normalize_tool_def(tool_def)
            self.tools.append(normalized)
            self.annotations[normalized["name"]] = annotation
        self._handlers = handlers

    def _connect_impl(self):
        return None

    def _call_tool_impl(self, tool_name: str, args: dict[str, Any]) -> str:
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}'"
        return _format_value(handler(**args))


class BytedServerMCPClient(MCPClient):
    def __init__(self, name: str, config: MCPConnectionConfig):
        super().__init__(name, config)
        self._client = None
        self._psm = config.psm or name

    async def _connect_async(self):
        from bytedance.mcp.mcp_client import byted_mcp_client_with_server_psm

        self._client = await byted_mcp_client_with_server_psm([self._psm])
        if hasattr(self._client, "set_client_connect_timeout"):
            self._client.set_client_connect_timeout(self._psm, self.config.connect_timeout)
        if hasattr(self._client, "set_client_request_timeout"):
            self._client.set_client_request_timeout(self._psm, self.config.request_timeout)
        await self._client.connect_to_servers()
        raw_tools = self._client.list_tools()
        raw_tools = await raw_tools if inspect.isawaitable(raw_tools) else raw_tools
        self.tools = []
        self.annotations = {}
        for raw_tool in raw_tools or []:
            normalized, annotation = _normalize_tool_def(raw_tool)
            self.tools.append(normalized)
            self.annotations[normalized["name"]] = annotation

    async def _call_tool_async(self, tool_name: str, args: dict[str, Any]) -> str:
        result = self._client.call_tool(tool_name, args)
        result = await result if inspect.isawaitable(result) else result
        return _format_mcp_result(result)

    async def _close_async(self):
        if self._client and hasattr(self._client, "aclose"):
            await self._client.aclose()
        self._client = None

    def _connect_impl(self):
        ASYNC_RUNTIME.run(self._connect_async())

    def _call_tool_impl(self, tool_name: str, args: dict[str, Any]) -> str:
        return ASYNC_RUNTIME.run(self._call_tool_async(tool_name, args))

    def _close_impl(self):
        if self._client:
            ASYNC_RUNTIME.run(self._close_async())


class StdioMCPClient(MCPClient):
    def __init__(self, name: str, config: MCPConnectionConfig):
        super().__init__(name, config)
        self._session = None
        self._exit_stack: AsyncExitStack | None = None

    async def _connect_async(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp import types as mcp_types
        from mcp.client.stdio import stdio_client

        client_kwargs = {"command": self.config.command}
        if self.config.args:
            client_kwargs["args"] = self.config.args
        if self.config.env:
            client_kwargs["env"] = self.config.env
        if self.config.cwd:
            client_kwargs["cwd"] = self.config.cwd

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        server_params = StdioServerParameters(**client_kwargs)
        read, write = await self._exit_stack.enter_async_context(stdio_client(server_params))
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(
                read_stream=read,
                write_stream=write,
                client_info=mcp_types.Implementation(
                    name="learn-claude-code.s19",
                    version="1.0",
                ),
            )
        )
        await self._session.initialize()
        response = await self._session.list_tools()
        self.tools = []
        self.annotations = {}
        for raw_tool in getattr(response, "tools", []) or []:
            normalized, annotation = _normalize_tool_def(raw_tool)
            self.tools.append(normalized)
            self.annotations[normalized["name"]] = annotation

    async def _call_tool_async(self, tool_name: str, args: dict[str, Any]) -> str:
        result = await self._session.call_tool(tool_name, args)
        return _format_mcp_result(result)

    async def _close_async(self):
        if self._exit_stack:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    def _connect_impl(self):
        if not self.config.command:
            raise ValueError("stdio transport requires command")
        ASYNC_RUNTIME.run(self._connect_async())

    def _call_tool_impl(self, tool_name: str, args: dict[str, Any]) -> str:
        return ASYNC_RUNTIME.run(self._call_tool_async(tool_name, args))

    def _close_impl(self):
        if self._exit_stack:
            ASYNC_RUNTIME.run(self._close_async())


mcp_clients: dict[str, MCPClient] = {}


def _mock_server_docs():
    client = MockMCPClient("docs")
    client.register(
        tool_defs=[
            {"name": "search", "description": "Search documentation. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]},
             "annotations": {"readOnlyHint": True, "teammateSafe": True}},
            {"name": "get_version", "description": "Get API version. (readOnly)",
             "inputSchema": {"type": "object", "properties": {},
                             "required": []},
             "annotations": {"readOnlyHint": True, "teammateSafe": True}},
        ],
        handlers={
            "search": lambda query: f"[docs] Found 3 results for '{query}'",
            "get_version": lambda: "[docs] API v2.1.0",
        })
    return client


def _mock_server_deploy():
    client = MockMCPClient("deploy")
    client.register(
        tool_defs=[
            {"name": "trigger",
             "description": "Trigger a deployment. (destructive — requires approval in real CC)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]},
             "annotations": {"destructiveHint": True, "teammateSafe": False,
                              "requiresApproval": True}},
            {"name": "status", "description": "Check deployment status. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]},
             "annotations": {"readOnlyHint": True, "teammateSafe": True}},
        ],
        handlers={
            "trigger": lambda service: f"[deploy] Triggered: {service}",
            "status": lambda service: f"[deploy] {service}: running (v1.4.2)",
        })
    return client


MOCK_SERVERS = {
    "docs": _mock_server_docs,
    "deploy": _mock_server_deploy,
}


def _register_client_tools(client_name: str, mcp_client: MCPClient):
    safe_server = normalize_mcp_name(client_name)
    for tool_def in mcp_client.tools:
        safe_tool = normalize_mcp_name(tool_def["name"])
        prefixed = f"mcp__{safe_server}__{safe_tool}"
        annotation = mcp_client.annotations.get(tool_def["name"], ToolAnnotation())
        MCP_TOOL_ANNOTATIONS[prefixed] = annotation
        MCP_TOOL_SOURCES[prefixed] = client_name


def _build_mcp_client(name: str, config: MCPConnectionConfig) -> MCPClient:
    if config.transport == "mock":
        factory = MOCK_SERVERS.get(name)
        if not factory:
            available = ", ".join(MOCK_SERVERS.keys())
            raise ValueError(f"Unknown mock MCP server '{name}'. Available: {available}")
        return factory()
    if config.transport == "server":
        return BytedServerMCPClient(name, config)
    if config.transport == "stdio":
        return StdioMCPClient(name, config)
    raise ValueError(f"Unsupported MCP transport '{config.transport}'")


def connect_mcp(name: str, transport: str = "mock", psm: str = "",
                command: str = "", args: list[str] | str | None = None,
                env: dict[str, str] | None = None, cwd: str = "",
                connect_timeout: int = 10,
                request_timeout: int = 600) -> str:
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"
    if isinstance(args, str):
        args = shlex.split(args)
    config = MCPConnectionConfig(
        transport=transport,
        psm=psm,
        command=command,
        args=list(args or []),
        env=dict(env or {}),
        cwd=cwd,
        connect_timeout=connect_timeout,
        request_timeout=request_timeout,
    )
    try:
        mcp_client = _build_mcp_client(name, config)
    except Exception as e:
        return str(e)
    result = mcp_client.connect()
    if not mcp_client.connected:
        return result
    mcp_clients[name] = mcp_client
    _register_client_tools(name, mcp_client)
    tool_names = [t["name"] for t in mcp_client.tools]
    print(f"  \033[31m[mcp] connected: {name} ({transport}) → {tool_names}\033[0m")
    return result


def assemble_tool_pool(base_tools: list[dict] | None = None,
                       base_handlers: dict[str, Callable] | None = None,
                       audience: str = "lead") -> tuple[list[dict], dict]:
    """Assemble builtin tools + allowed MCP tools into one pool."""
    tools = list(base_tools or BUILTIN_TOOLS)
    handlers = dict(base_handlers or BUILTIN_HANDLERS)
    for server_name, mcp_client in mcp_clients.items():
        safe_server = normalize_mcp_name(server_name)
        for tool_def in mcp_client.tools:
            safe_tool = normalize_mcp_name(tool_def["name"])
            prefixed = f"mcp__{safe_server}__{safe_tool}"
            annotation = mcp_client.annotations.get(tool_def["name"], ToolAnnotation())
            MCP_TOOL_ANNOTATIONS[prefixed] = annotation
            MCP_TOOL_SOURCES[prefixed] = server_name
            if audience != "lead" and not annotation.teammate_safe:
                continue
            tools.append({
                "name": prefixed,
                "description": tool_def.get("description", "") + _describe_annotation(annotation),
                "input_schema": tool_def.get("inputSchema", {}),
            })
            handlers[prefixed] = (
                lambda *, c=mcp_client, t=tool_def["name"], **kw: c.call_tool(t, kw))
    return tools, handlers


def close_all_mcp_clients():
    for mcp_client in list(mcp_clients.values()):
        mcp_client.close()


atexit.register(ASYNC_RUNTIME.close)
atexit.register(close_all_mcp_clients)


# ── Lead Worktree Tools ──

def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)

def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)

def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)


# ── Basic tool handlers ──

def run_create_task(subject: str, description: str = "",
                    blockedBy: list[str] | None = None) -> str:
    task = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"  {t.id}: {t.subject} [{t.status}]"
        + (f" (wt:{t.worktree})" if t.worktree else "")
        for t in tasks)


def run_get_task(task_id: str) -> str:
    return get_task_json(task_id)

def run_claim_task(task_id: str) -> str:
    return claim_task(task_id, owner="agent")

def run_complete_task(task_id: str) -> str:
    return complete_task(task_id)

def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)

def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Sent to {to}"

def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f" [{m['type']} req:{req_id}]" if req_id else f" [{m['type']}]"
        lines.append(f"  [{m['from']}]{tag} {m['content'][:200]}")
    return "\n".join(lines)

def run_connect_mcp(name: str, transport: str = "mock", psm: str = "",
                    command: str = "", args: list[str] | str | None = None,
                    env: dict[str, str] | None = None, cwd: str = "",
                    connect_timeout: int = 10,
                    request_timeout: int = 600) -> str:
    return connect_mcp(
        name=name,
        transport=transport,
        psm=psm,
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        connect_timeout=connect_timeout,
        request_timeout=request_timeout,
    )


def ensure_default_teammates() -> list[str]:
    results = []
    for config in DEFAULT_TEAMMATES:
        results.append(
            spawn_teammate_thread(
                name=config["name"],
                role=config["role"],
                prompt=config["prompt"],
                idle_timeout=config.get("idle_timeout"),
            )
        )
    return results


# ── Tool Definitions ──

BUILTIN_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "limit": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "create_task", "description": "Create a task.",
     "input_schema": {"type": "object",
                      "properties": {"subject": {"type": "string"},
                                     "description": {"type": "string"},
                                     "blockedBy": {"type": "array",
                                                   "items": {"type": "string"}}},
                      "required": ["subject"]}},
    {"name": "list_tasks", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_task", "description": "Get full task details.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "claim_task", "description": "Claim a pending task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "complete_task", "description": "Complete an in-progress task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "spawn_teammate", "description": "Spawn an autonomous teammate.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "role": {"type": "string"},
                                     "prompt": {"type": "string"}},
                      "required": ["name", "role", "prompt"]}},
    {"name": "send_message", "description": "Send message to a teammate.",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["to", "content"]}},
    {"name": "check_inbox",
     "description": "Check inbox for messages and protocol responses.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "request_shutdown",
     "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"}},
                      "required": ["teammate"]}},
    {"name": "request_plan",
     "description": "Ask a teammate to submit a plan.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"},
                                     "task": {"type": "string"}},
                      "required": ["teammate", "task"]}},
    {"name": "review_plan",
     "description": "Approve or reject a submitted plan.",
     "input_schema": {"type": "object",
                      "properties": {"request_id": {"type": "string"},
                                     "approve": {"type": "boolean"},
                                     "feedback": {"type": "string"}},
                      "required": ["request_id", "approve"]}},
    {"name": "create_worktree",
     "description": "Create an isolated git worktree.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "task_id": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "remove_worktree",
     "description": "Remove a worktree. Refuses if changes exist.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "discard_changes": {"type": "boolean"}},
                      "required": ["name"]}},
    {"name": "keep_worktree",
     "description": "Keep a worktree for manual review.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "connect_mcp",
     "description": "Connect to an MCP transport. "
                    "mock: docs/deploy, server: byted PSM, stdio: local MCP command.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "transport": {"type": "string"},
                                     "psm": {"type": "string"},
                                     "command": {"type": "string"},
                                     "args": {"type": "array",
                                              "items": {"type": "string"}},
                                     "env": {"type": "object"},
                                     "cwd": {"type": "string"},
                                     "connect_timeout": {"type": "integer"},
                                     "request_timeout": {"type": "integer"}},
                      "required": ["name"]}},
]

BUILTIN_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "create_task": run_create_task, "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task, "complete_task": run_complete_task,
    "spawn_teammate": run_spawn_teammate,
    "send_message": run_send_message, "check_inbox": run_check_inbox,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
    "connect_mcp": run_connect_mcp,
}


# ── Context ──

MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"


def update_context(context: dict, messages: list) -> dict:
    memories = ""
    if MEMORY_INDEX.exists():
        memories = MEMORY_INDEX.read_text()[:2000]
    return {"memories": memories}


# ── Agent Loop (s19: dynamic tool pool, no prompt cache) ──

def agent_loop(messages: list, context: dict):
    tools, handlers = assemble_tool_pool()
    system = assemble_system_prompt(context)
    while True:
        try:
            response = client.messages.create(
                model=MODEL, system=system, messages=messages,
                tools=tools, max_tokens=8000)
        except Exception as e:
            messages.append({"role": "assistant", "content": [
                {"type": "text", "text": f"[Error] {type(e).__name__}: {e}"}]})
            return

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\033[36m> {block.name}\033[0m")
            output = dispatch_tool_use(block, handlers, actor="lead")
            print(str(output)[:300])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})

        if any(b.name == "connect_mcp" for b in response.content
               if b.type == "tool_use"):
            tools, handlers = assemble_tool_pool()
            context = update_context(context, messages)
            system = assemble_system_prompt(context)


if __name__ == "__main__":
    print("s19: mcp tools")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    boot_results = ensure_default_teammates()
    for line in boot_results:
        print(f"[boot] {line}")
    if boot_results:
        print()
    history = []
    context = {"memories": ""}
    while True:
        try:
            query = input("\033[36ms19 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history, context)
        context = update_context(context, history)
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                print(block.get("text", ""))

        inbox = consume_lead_inbox(route_protocol=True)
        if inbox:
            inbox_text = "\n".join(
                f"From {m['from']} [{m.get('type', 'message')}]: "
                f"{m['content'][:200]}" for m in inbox)
            history.append({"role": "user",
                            "content": f"[Inbox]\n{inbox_text}"})
        print()
