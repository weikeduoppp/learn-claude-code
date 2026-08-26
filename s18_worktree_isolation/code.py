#!/usr/bin/env python3
"""
s18: Worktree Isolation — git worktree + task-directory binding + event log.

Run:  python s18_worktree_isolation/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s17:
  - Task dataclass gains worktree field (str | None)
  - validate_worktree_name: reject path traversal and illegal chars
  - create_worktree: validate name, git worktree add, optional task binding
  - bind_task_to_worktree: write worktree field only, keep task pending
  - remove_worktree: safety check before force, no auto-complete
  - run_git returns (ok, output), events only on success
  - Teammate tools: + complete_task, run in worktree cwd when bound
  - scan_unclaimed_tasks: uses can_start() for dependency checking
  - idle_poll: checks claim result, dispatches shutdown in IDLE
  - consume_lead_inbox: unified inbox consumer
  - 3 new Lead tools: create_worktree, remove_worktree, keep_worktree

ASCII topology:
  Main repo (/)
    ├── .worktrees/auth/  (branch: wt/auth)  ← Task #1
    ├── .worktrees/ui/    (branch: wt/ui)     ← Task #2
    ├── .tasks/task_xxx.json (worktree: "auth")
    └── .worktrees/events.jsonl
"""
from __future__ import annotations


import os, subprocess, json, time, random, threading, re, inspect
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field

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

# ── Task System (from s12 + s18 worktree field) ──

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
    worktree: str | None = None      # s18: bound worktree name


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
    task = load_task(task_id)
    return json.dumps(asdict(task), indent=2)


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


def release_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot release"
    task.status = "pending"
    task.owner = None
    save_task(task)
    print(f"  \033[33m[release] {task.subject} → pending\033[0m")
    return f"Released {task.id} ({task.subject}) back to pending"


# ── Worktree System (s18 new) ──

WORKTREES_DIR = WORKDIR / ".worktrees"
WORKTREES_DIR.mkdir(exist_ok=True)

VALID_WT_NAME = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def validate_worktree_name(name: str) -> str | None:
    """Return error message if invalid, None if valid."""
    if not name:
        return "Worktree name cannot be empty"
    if name == "." or name == "..":
        return f"'{name}' is not a valid worktree name"
    if not VALID_WT_NAME.match(name):
        return (f"Invalid worktree name '{name}': "
                "only letters, digits, dots, underscores, dashes (1-64 chars)")
    return None


def run_git(args: list[str]) -> tuple[bool, str]:
    """Run git command. Return (ok, output)."""
    try:
        r = subprocess.run(["git"] + args, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout + r.stderr).strip()
        out = out[:5000] if out else "(no output)"
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Error: git timeout"


def log_event(event_type: str, worktree_name: str, task_id: str = ""):
    """Append a lifecycle event to events.jsonl."""
    event = {"type": event_type, "worktree": worktree_name,
             "task_id": task_id, "ts": time.time()}
    events_file = WORKTREES_DIR / "events.jsonl"
    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")


LOG_DIR = WORKDIR / ".logs"
LOG_DIR.mkdir(exist_ok=True)
RUN_LOG_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
BUS_LOG_FILE = LOG_DIR / f"s18_{RUN_LOG_STAMP}_bus.jsonl"
RUNTIME_LOG_FILE = LOG_DIR / f"s18_{RUN_LOG_STAMP}_runtime.jsonl"
KEY_LOG_FILE = LOG_DIR / f"s18_{RUN_LOG_STAMP}_key_logs.md"


def append_jsonl(path: Path, payload: dict):
    with open(path, "a") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def log_runtime_event(event_type: str, **payload):
    append_jsonl(RUNTIME_LOG_FILE, {
        "type": event_type,
        "ts": time.time(),
        **payload,
    })


def read_jsonl_tail(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    items = []
    for line in lines[-max(1, limit):]:
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return items


def create_worktree(name: str, task_id: str = "") -> str:
    """Create a git worktree with a dedicated branch. Optionally bind to a task."""
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
    """Write worktree field to task. Keep status as pending for auto-claim."""
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)
    print(f"  \033[33m[bind] {task.subject} → worktree:{worktree_name}\033[0m")


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    """Count uncommitted files and commits in a worktree."""
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
    """Remove worktree. Refuses if uncommitted changes unless discard_changes."""
    err = validate_worktree_name(name)
    if err:
        return err
    path = WORKTREES_DIR / name
    if not path.exists():
        return f"Worktree '{name}' not found"
    if not discard_changes:
        files, commits = _count_worktree_changes(path)
        if files < 0:
            return (f"Cannot verify worktree '{name}' status. "
                    "Use discard_changes=true to force removal.")
        if files > 0 or commits > 0:
            return (f"Worktree '{name}' has {files} uncommitted file(s) "
                    f"and {commits} unpushed commit(s). "
                    "Use discard_changes=true to force removal, "
                    "or keep_worktree to preserve for review.")
    ok1, _ = run_git(["worktree", "remove", str(path), "--force"])
    if not ok1:
        return f"Failed to remove worktree directory for '{name}'"
    run_git(["branch", "-D", f"wt/{name}"])
    log_event("remove", name)
    print(f"  \033[33m[worktree] removed: {name}\033[0m")
    return f"Worktree '{name}' removed"


def keep_worktree(name: str) -> str:
    """Keep worktree for manual review. Branch preserved."""
    err = validate_worktree_name(name)
    if err:
        return err
    log_event("keep", name)
    print(f"  \033[36m[worktree] kept: {name}\033[0m")
    return f"Worktree '{name}' kept for review (branch: wt/{name})"


def auto_dispose_completed_worktree(task_id: str, worktree_name: str = "") -> str:
    """Lead policy for completed tasks: keep changed worktrees, remove clean ones."""
    if not worktree_name and _task_path(task_id).exists():
        worktree_name = load_task(task_id).worktree or ""
    if not worktree_name:
        return "No bound worktree; no keep/remove action needed."

    err = validate_worktree_name(worktree_name)
    if err:
        return f"Cannot auto-dispose worktree: {err}"

    path = WORKTREES_DIR / worktree_name
    if not path.exists():
        return f"Bound worktree '{worktree_name}' is already missing; nothing to dispose."

    files, commits = _count_worktree_changes(path)
    if files < 0:
        result = keep_worktree(worktree_name)
        return ("Auto-kept worktree for manual review because status could not be "
                f"verified. {result}")

    if files == 0 and commits == 0:
        result = remove_worktree(worktree_name)
        return ("Auto-removed clean completed worktree because it has no "
                f"uncommitted files or unpushed commits. {result}")

    result = keep_worktree(worktree_name)
    return (f"Auto-kept worktree for review because it has {files} "
            f"uncommitted file(s) and {commits} unpushed commit(s). {result}")


# ── Prompt Assembly (from s10) ──

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, "
             "create_task, list_tasks, get_task, claim_task, release_task, complete_task, "
             "spawn_teammate, list_teammates, send_message, check_inbox, "
             "request_shutdown, request_plan, review_plan, continue_teammate_work, "
             "extract_key_logs, "
             "create_worktree, remove_worktree, keep_worktree.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    sections = [PROMPT_SECTIONS["identity"],
                PROMPT_SECTIONS["tools"],
                PROMPT_SECTIONS["workspace"]]
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")
    return "\n\n".join(sections)


_last_context_hash, _last_prompt = None, None


def get_system_prompt(context: dict) -> str:
    global _last_context_hash, _last_prompt
    h = json.dumps(context, sort_keys=True)
    if h == _last_context_hash and _last_prompt:
        return _last_prompt
    _last_context_hash, _last_prompt = h, assemble_system_prompt(context)
    return _last_prompt


# ── Basic Tools ──

def safe_path(p: str, cwd: Path = None) -> Path:
    base = cwd or WORKDIR
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str, cwd: Path = None,
             timeout_ms: int | None = None,
             timeout: int | float | None = None) -> str:
    timeout_s = 120
    if timeout_ms is not None:
        timeout_s = max(1, min(int(timeout_ms), 300000)) / 1000
    elif timeout is not None:
        timeout_s = max(1, min(float(timeout), 300))
    try:
        r = subprocess.run(command, shell=True, cwd=cwd or WORKDIR,
                           capture_output=True, text=True, timeout=timeout_s)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout ({timeout_s:g}s)"


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


def invoke_handler(handler, tool_input: dict | None):
    """Call a tool handler while tolerating extra model-supplied fields."""
    tool_input = tool_input or {}
    sig = inspect.signature(handler)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD
           for p in sig.parameters.values()):
        return handler(**tool_input)
    accepted = {
        name for name, param in sig.parameters.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          inspect.Parameter.KEYWORD_ONLY)
    }
    filtered = {k: v for k, v in tool_input.items() if k in accepted}
    ignored = sorted(set(tool_input) - accepted)
    if ignored:
        print(f"  \033[33m[tool args] ignored for {handler.__name__}: "
              f"{', '.join(ignored)}\033[0m")
    return handler(**filtered)


def format_labeled_block(prefix: str, content: str) -> str:
    text = str(content).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines() or [""]
    head = f"{prefix}{lines[0]}"
    if len(lines) == 1:
        return head
    tail = "\n".join(f"      {line}" for line in lines[1:])
    return f"{head}\n{tail}"


# ── MessageBus (from s15) ──

MAILBOX_DIR = WORKDIR / ".mailboxes"
MAILBOX_DIR.mkdir(exist_ok=True)
TEAMMATE_REGISTRY = WORKDIR / ".teammates.json"


class MessageBus:
    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        msg = {"from": from_agent, "to": to_agent,
               "content": content, "type": msg_type,
               "ts": time.time(), "metadata": metadata or {}}
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a") as f:
            f.write(json.dumps(msg) + "\n")
        append_jsonl(BUS_LOG_FILE, msg)
        preview = format_labeled_block(
            f"  \033[33m[bus] {from_agent} → {to_agent}: ({msg_type}) ",
            str(content),
        )
        print(f"{preview}\033[0m")

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
awaiting_lead_teammates: set[str] = set()
idle_teammates: set[str] = set()
teammate_wake_events: dict[str, threading.Event] = {}
teammate_threads: dict[str, threading.Thread] = {}
_task_watcher_started = False
_task_watcher_lock = threading.Lock()


def load_teammate_specs() -> dict[str, dict[str, str]]:
    if not TEAMMATE_REGISTRY.exists():
        return {}
    try:
        data = json.loads(TEAMMATE_REGISTRY.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    specs = {}
    for name, spec in data.items():
        if not isinstance(spec, dict):
            continue
        role = spec.get("role")
        prompt = spec.get("prompt")
        if isinstance(role, str) and isinstance(prompt, str):
            specs[name] = {"role": role, "prompt": prompt}
    return specs


def save_teammate_specs():
    TEAMMATE_REGISTRY.write_text(
        json.dumps(teammate_specs, ensure_ascii=False, indent=2) + "\n"
    )


teammate_specs: dict[str, dict[str, str]] = load_teammate_specs()


def sync_teammate_liveness():
    stale = []
    for name, thread in list(teammate_threads.items()):
        if thread.is_alive():
            continue
        stale.append(name)
    for name in stale:
        log_runtime_event("teammate_stale_cleanup", teammate=name)
        teammate_threads.pop(name, None)
        active_teammates.pop(name, None)
        awaiting_lead_teammates.discard(name)
        idle_teammates.discard(name)

# ── Protocol State (from s16) ──

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
        print(f"  \033[31m[protocol] unknown request_id: {request_id}\033[0m")
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        print(f"  \033[31m[protocol] type mismatch: expected shutdown_response, "
              f"got {response_type}\033[0m")
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        print(f"  \033[31m[protocol] type mismatch: expected plan_approval_response, "
              f"got {response_type}\033[0m")
        return
    state.status = "approved" if approve else "rejected"
    icon = "✓" if approve else "✗"
    color = "32" if approve else "31"
    print(f"  \033[{color}m[protocol] {state.type} {icon} "
          f"({request_id}: {state.status})\033[0m")


def consume_lead_inbox(route_protocol=True) -> list[dict]:
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                match_response(msg_type, req_id, meta.get("approve", False))
            if msg_type == "task_completed":
                task_id = meta.get("task_id", "")
                worktree_name = meta.get("worktree", "")
                disposition = auto_dispose_completed_worktree(task_id, worktree_name)
                msg["content"] = f"{msg['content']}\n[lead auto-disposition] {disposition}"
    return msgs


# ── Autonomous Agent (from s17, + worktree cwd) ──

IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60
AWAIT_LEAD_TIMEOUT = 300
TASK_WATCH_POLL_INTERVAL = 0.2
TASK_WATCH_DEBOUNCE = 1.0


def scan_unclaimed_tasks() -> list[dict]:
    """Find pending, unowned tasks with all dependencies completed."""
    unclaimed = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        task = json.loads(f.read_text())
        if (task.get("status") == "pending"
                and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed


def _task_board_snapshot() -> tuple[tuple[str, int, int], ...]:
    snapshot = []
    for path in sorted(TASKS_DIR.glob("task_*.json")):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        snapshot.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(snapshot)


def wake_idle_teammates(reason: str):
    sync_teammate_liveness()
    targets = [name for name in sorted(idle_teammates)
               if name in active_teammates]
    if not targets:
        return
    for name in targets:
        teammate_wake_events.setdefault(name, threading.Event()).set()
    print(f"  \033[35m[task watcher] {reason} → wake idle teammates: "
          f"{', '.join(targets)}\033[0m")


def task_watcher_loop():
    last_snapshot = _task_board_snapshot()
    pending_change_at = None
    pending_reason = "task board changed"
    while True:
        time.sleep(TASK_WATCH_POLL_INTERVAL)
        current = _task_board_snapshot()
        if current != last_snapshot:
            last_snapshot = current
            pending_change_at = time.time()
            continue
        if pending_change_at is None:
            continue
        if time.time() - pending_change_at < TASK_WATCH_DEBOUNCE:
            continue
        pending_change_at = None
        wake_idle_teammates(pending_reason)


def ensure_task_watcher_started():
    global _task_watcher_started
    with _task_watcher_lock:
        if _task_watcher_started:
            return
        threading.Thread(target=task_watcher_loop, daemon=True).start()
        _task_watcher_started = True
        log_runtime_event("task_watcher_started")
        print("  \033[35m[task watcher] started\033[0m")


def maybe_respawn_teammate(name: str) -> str | None:
    sync_teammate_liveness()
    if name == "lead" or name in active_teammates:
        return None
    spec = teammate_specs.get(name)
    if not spec:
        return None
    log_runtime_event("teammate_respawn_requested", teammate=name,
                      role=spec["role"])
    print(f"  \033[35m[respawn] restarting teammate '{name}'\033[0m")
    return spawn_teammate_thread(name, spec["role"], spec["prompt"])


def send_agent_message(from_agent: str, to_agent: str, content: str,
                       msg_type: str = "message",
                       metadata: dict | None = None) -> str:
    sync_teammate_liveness()
    was_active = to_agent == "lead" or to_agent in active_teammates
    respawn_note = maybe_respawn_teammate(to_agent)
    BUS.send(from_agent, to_agent, content, msg_type, metadata)
    if respawn_note:
        return f"Sent to {to_agent} ({respawn_note})"
    if was_active:
        return f"Sent to {to_agent}"
    if to_agent in teammate_specs:
        return f"Sent to {to_agent} (known teammate was offline; respawn attempted)"
    if to_agent == "lead":
        return "Sent to lead"
    return f"Sent to {to_agent} (unknown teammate; no respawn spec)"


def list_teammates() -> str:
    sync_teammate_liveness()
    names = sorted(set(teammate_specs) | set(active_teammates))
    if not names:
        return "No known teammates."
    lines = []
    for name in names:
        role = teammate_specs.get(name, {}).get("role", "unknown")
        if name in active_teammates:
            if name in awaiting_lead_teammates:
                state = "awaiting_lead"
            elif name in idle_teammates:
                state = "idle"
            else:
                state = "active"
        else:
            state = "offline"
        lines.append(f"  {name}: {state} role={role}")
    return "\n".join(lines)


def send_idle_notification(name: str, role: str):
    BUS.send(
        name,
        "lead",
        f"{name} ({role}) is idle and ready for more work.",
        "idle_notification",
        {"state": "idle", "role": role},
    )


def extract_text_blocks(content) -> str:
    parts = []
    if isinstance(content, str):
        parts.append(content.strip())
    elif isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if getattr(block, "type", None) == "text" and isinstance(text, str):
                parts.append(text.strip())
    return "\n".join(p for p in parts if p).strip()


def idle_poll(agent_name: str, messages: list,
              name: str, role: str,
              allow_auto_claim: bool = True) -> tuple[str, str | None]:
    """Poll for 60s. Return (result, auto_claimed_task_id)."""
    wake_event = teammate_wake_events.setdefault(agent_name, threading.Event())
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        wake_event.wait(IDLE_POLL_INTERVAL)
        wake_event.clear()

        inbox = BUS.read_inbox(agent_name)
        if inbox:
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(name, "lead", "Shutting down gracefully.",
                             "shutdown_response",
                             {"request_id": req_id, "approve": True})
                    print(f"  \033[35m[protocol] {name} approved shutdown "
                          f"in idle ({req_id})\033[0m")
                    return "shutdown", None

            messages.append({"role": "user",
                "content": "<inbox>" + json.dumps(inbox) + "</inbox>"})
            print(f"  \033[36m[idle] {name} found inbox messages\033[0m")
            return "work", None

        unclaimed = scan_unclaimed_tasks() if allow_auto_claim else []
        if unclaimed:
            task_data = unclaimed[0]
            result = claim_task(task_data["id"], agent_name)
            if "Claimed" in result:
                wt_info = ""
                if task_data.get("worktree"):
                    wt_path = WORKTREES_DIR / task_data["worktree"]
                    wt_info = f"\nWork directory: {wt_path}"
                messages.append({"role": "user",
                    "content": f"<auto-claimed>Task {task_data['id']}: "
                               f"{task_data['subject']}{wt_info}\n"
                               f"When finished, call complete_task('{task_data['id']}'). "
                               f"If blocked or stopping, send a status update and "
                               f"release_task('{task_data['id']}').</auto-claimed>"})
                print(f"  \033[32m[idle] {name} auto-claimed: "
                      f"{task_data['subject']}\033[0m")
                return "work", task_data["id"]
            print(f"  \033[33m[idle] {name} claim failed: "
                  f"{result}\033[0m")

    print(f"  \033[31m[idle] {name} timeout ({IDLE_TIMEOUT}s)\033[0m")
    return "timeout", None


def await_lead_poll(agent_name: str, messages: list, name: str,
                    role: str, task: Task, pause_info: dict) -> str:
    wake_event = teammate_wake_events.setdefault(agent_name, threading.Event())
    awaiting_lead_teammates.add(name)
    idle_teammates.discard(name)
    summary = pause_info.get("summary", "").strip() or "(no summary)"
    next_step = pause_info.get("next_step", "").strip() or "Review and decide whether to continue."
    reason = pause_info.get("reason", "unknown")
    content = (
        f"Task {task.id} ({task.subject}) yielded to lead.\n"
        f"Reason: {reason}\n"
        f"Summary: {summary}\n"
        f"Next step: {next_step}"
    )
    BUS.send(
        name,
        "lead",
        content,
        "task_yield",
        {
            "task_id": task.id,
            "worktree": task.worktree or "",
            "reason": reason,
            "role": role,
        },
    )
    log_runtime_event("await_lead_enter", teammate=name, role=role,
                      task_id=task.id, worktree=task.worktree or "",
                      reason=reason, summary=summary, next_step=next_step)
    print(f"  \033[35m[await] {name} waiting for lead on {task.id} ({reason})\033[0m")

    try:
        for _ in range(AWAIT_LEAD_TIMEOUT // IDLE_POLL_INTERVAL):
            wake_event.wait(IDLE_POLL_INTERVAL)
            wake_event.clear()

            inbox = BUS.read_inbox(agent_name)
            if not inbox:
                continue

            should_resume = False
            non_protocol = []
            for msg in inbox:
                msg_type = msg.get("type")
                if msg_type == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(name, "lead", "Shutting down gracefully.",
                             "shutdown_response",
                             {"request_id": req_id, "approve": True})
                    print(f"  \033[35m[protocol] {name} approved shutdown "
                          f"while awaiting lead ({req_id})\033[0m")
                    return "shutdown"
                if msg_type == "continue_work":
                    directive = msg.get("content", "").strip()
                    messages.append({
                        "role": "user",
                        "content": (
                            f"<lead_decision>Continue task {task.id} "
                            f"({task.subject}). {directive}</lead_decision>"
                        ),
                    })
                    should_resume = True
                    continue
                non_protocol.append(msg)

            if non_protocol:
                messages.append({
                    "role": "user",
                    "content": "<inbox>" + json.dumps(non_protocol) + "</inbox>",
                })
                should_resume = True

            if should_resume:
                BUS.send(
                    name,
                    "lead",
                    f"Resuming work on {task.id} ({task.subject}).",
                    "task_resumed",
                    {"task_id": task.id, "worktree": task.worktree or ""},
                )
                log_runtime_event("await_lead_resume", teammate=name,
                                  task_id=task.id, worktree=task.worktree or "")
                print(f"  \033[36m[await] {name} resuming {task.id}\033[0m")
                return "continue"
    finally:
        awaiting_lead_teammates.discard(name)

    print(f"  \033[31m[await] {name} timed out waiting for lead ({task.id})\033[0m")
    log_runtime_event("await_lead_timeout", teammate=name, task_id=task.id,
                      worktree=task.worktree or "")
    return "timeout"


# ── Teammate Thread (from s15 + s16 + s17 + s18) ──

def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    sync_teammate_liveness()
    if name in active_teammates:
        return f"Teammate '{name}' already exists"
    ensure_task_watcher_started()
    teammate_specs[name] = {"role": role, "prompt": prompt}
    save_teammate_specs()
    teammate_wake_events.setdefault(name, threading.Event())

    system = (f"You are '{name}', a {role}. "
              f"Use tools to complete tasks. "
              f"You can list and claim tasks from the board. "
              f"If a task has a worktree, work in that directory. "
              f"When you finish a claimed task, call complete_task(task_id) "
              f"before going idle or exiting. "
              f"If your current task is not finished but you pause, provide a concise "
              f"summary and likely next step so lead can decide whether to continue. "
              f"If you stop with unfinished work and want to give it up, send a status "
              f"update to lead and call release_task(task_id).")

    def handle_inbox_message(name: str, msg: dict, messages: list):
        msg_type = msg.get("type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")

        if msg_type == "shutdown_request":
            BUS.send(name, "lead", "Shutting down gracefully.",
                     "shutdown_response",
                     {"request_id": req_id, "approve": True})
            print(f"  \033[35m[protocol] {name} approved shutdown "
                  f"({req_id})\033[0m")
            return "shutdown"

        if msg_type == "plan_approval_response":
            approve = meta.get("approve", False)
            if approve:
                messages.append({"role": "user",
                    "content": "[Plan approved] Proceed with the task."})
            else:
                messages.append({"role": "user",
                    "content": f"[Plan rejected] Feedback: {msg['content']}"})
            return "continue"

        if msg_type == "continue_work":
            messages.append({"role": "user",
                "content": f"<lead_decision>{msg.get('content', '')}</lead_decision>"})
            return "continue"

        return None

    def run():
        # Track current worktree for this teammate's cwd
        wt_ctx = {"path": None}
        task_ctx = {"id": None}

        def _current_task() -> Task | None:
            task_id = task_ctx["id"]
            if not task_id or not _task_path(task_id).exists():
                return None
            task = load_task(task_id)
            return task if task.status == "in_progress" else None

        def _wt_cwd() -> Path | None:
            p = wt_ctx["path"]
            return Path(p) if p else None

        def _run_bash(command: str,
                      timeout_ms: int | None = None,
                      timeout: int | float | None = None) -> str:
            return run_bash(command, cwd=_wt_cwd(),
                            timeout_ms=timeout_ms, timeout=timeout)

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
                task_ctx["id"] = task_id
                # Set worktree cwd if task has one
                task = load_task(task_id)
                if task.worktree:
                    wt_ctx["path"] = str(WORKTREES_DIR / task.worktree)
                else:
                    wt_ctx["path"] = None
                BUS.send(
                    name,
                    "lead",
                    f"Claimed {task.id} ({task.subject})"
                    + (f" in {wt_ctx['path']}" if wt_ctx["path"] else ""),
                    "task_claimed",
                    {"task_id": task.id, "worktree": task.worktree or ""},
                )
                return (result
                        + f"\nWhen finished, call complete_task('{task_id}').")
            return result

        def _run_release_task(task_id: str):
            result = release_task(task_id)
            if "Released" in result:
                task_ctx["id"] = None
                wt_ctx["path"] = None
                BUS.send(name, "lead", result, "task_released",
                         {"task_id": task_id})
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            if "Completed" in result:
                task = load_task(task_id) if _task_path(task_id).exists() else None
                task_ctx["id"] = None
                wt_ctx["path"] = None
                BUS.send(name, "lead", result, "task_completed",
                         {"task_id": task_id,
                          "worktree": (task.worktree if task else "") or ""})
            return result

        messages = [{"role": "user", "content": prompt}]
        sub_tools = [
            {"name": "bash", "description": "Run a shell command.",
             "input_schema": {"type": "object",
                              "properties": {
                                  "command": {"type": "string"},
                                  "timeout_ms": {"type": "integer"},
                                  "timeout": {"type": "number"},
                              },
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
             "description": "List all tasks on the board.",
             "input_schema": {"type": "object", "properties": {},
                              "required": []}},
            {"name": "claim_task",
             "description": "Claim a pending task.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
            {"name": "release_task",
             "description": "Release an in-progress task back to pending.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
            {"name": "complete_task",
             "description": "Mark an in-progress task as completed.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
        ]

        sub_handlers = {
            "bash": _run_bash, "read_file": _run_read,
            "write_file": _run_write,
            "send_message": lambda to, content: send_agent_message(name, to, content),
            "submit_plan": lambda plan: _teammate_submit_plan(name, plan),
            "list_tasks": _run_list_tasks,
            "claim_task": _run_claim_task,
            "release_task": _run_release_task,
            "complete_task": _run_complete_task,
        }

        try:
            # Outer loop: WORK → IDLE cycle
            while True:
                if len(messages) <= 3:
                    messages.insert(0, {"role": "user",
                        "content": f"<identity>You are '{name}', role: {role}. "
                                   f"Continue your work.</identity>"})

                # WORK phase
                should_shutdown = False
                work_pause = None
                for _ in range(10):
                    inbox = BUS.read_inbox(name)
                    for msg in inbox:
                        action = handle_inbox_message(name, msg, messages)
                        if action == "shutdown":
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
                        response = client.messages.create(
                            model=MODEL, system=system, messages=messages[-20:],
                            tools=sub_tools, max_tokens=8000)
                    except Exception as e:
                        task = _current_task()
                        if task:
                            work_pause = {
                                "reason": "llm_error",
                                "summary": f"{type(e).__name__}: {e}",
                                "next_step": "Review the model error and decide whether to continue the task.",
                            }
                        break
                    messages.append({"role": "assistant", "content": response.content})
                    if response.stop_reason != "tool_use":
                        task = _current_task()
                        if task:
                            assistant_summary = extract_text_blocks(response.content)
                            work_pause = {
                                "reason": "no_tool_use_with_in_progress_task",
                                "summary": assistant_summary or "(assistant returned no text)",
                                "next_step": (
                                    "Review this intermediate report. "
                                    "If the task should continue, send continue_teammate_work."
                                ),
                            }
                        break
                    results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            handler = sub_handlers.get(block.name)
                            output = (invoke_handler(handler, block.input)
                                      if handler else "Unknown")
                            results.append({"type": "tool_result",
                                            "tool_use_id": block.id,
                                            "content": str(output)})
                    messages.append({"role": "user", "content": results})
                else:
                    task = _current_task()
                    if task:
                        work_pause = {
                            "reason": "work_round_limit_reached",
                            "summary": "Reached the 10-round work limit while the task is still in progress.",
                            "next_step": (
                                "Review recent tool outputs. "
                                "If the task should continue, send continue_teammate_work."
                            ),
                        }

                if should_shutdown:
                    break

                current_task = _current_task()
                if current_task:
                    if not work_pause:
                        work_pause = {
                            "reason": "task_still_in_progress_after_work_phase",
                            "summary": "The current task remains in progress after this work phase.",
                            "next_step": (
                                "Review the current task state. "
                                "If the task should continue, send continue_teammate_work."
                            ),
                        }
                    await_result = await_lead_poll(
                        name, messages, name, role, current_task, work_pause)
                    if await_result == "continue":
                        continue
                    if await_result == "shutdown":
                        break
                    if await_result == "timeout":
                        release_result = release_task(current_task.id)
                        BUS.send(
                            name,
                            "lead",
                            f"Awaiting lead timed out; {release_result}",
                            "task_released",
                            {"task_id": current_task.id,
                             "reason": "await_lead_timeout"},
                        )
                        task_ctx["id"] = None
                        wt_ctx["path"] = None
                        BUS.send(
                            name,
                            "lead",
                            f"{name} shut down after waiting too long for lead.",
                            "teammate_shutdown",
                            {"reason": "await_lead_timeout", "role": role},
                        )
                        break

                # IDLE phase
                if not current_task:
                    idle_teammates.add(name)
                    teammate_wake_events.setdefault(name, threading.Event()).clear()
                    send_idle_notification(name, role)
                idle_result, claimed_task_id = idle_poll(
                    name, messages, name, role, allow_auto_claim=True)
                idle_teammates.discard(name)
                if idle_result == "shutdown":
                    break
                if idle_result == "timeout":
                    current_task_id = task_ctx["id"]
                    if current_task_id and _task_path(current_task_id).exists():
                        current_task = load_task(current_task_id)
                        if current_task.status == "in_progress":
                            release_result = release_task(current_task_id)
                            BUS.send(
                                name,
                                "lead",
                                f"Idle timeout; {release_result}",
                                "task_released",
                                {"task_id": current_task_id,
                                 "reason": "idle_timeout"},
                            )
                            task_ctx["id"] = None
                            wt_ctx["path"] = None
                    BUS.send(
                        name,
                        "lead",
                        f"{name} shut down after idle timeout.",
                        "teammate_shutdown",
                        {"reason": "idle_timeout", "role": role},
                    )
                    break
                if idle_result == "work" and claimed_task_id:
                    task_ctx["id"] = claimed_task_id
                    task = load_task(claimed_task_id)
                    if task.worktree:
                        wt_ctx["path"] = str(WORKTREES_DIR / task.worktree)
                    else:
                        wt_ctx["path"] = None
                    BUS.send(
                        name,
                        "lead",
                        f"Auto-claimed {task.id} ({task.subject})"
                        + (f" in {wt_ctx['path']}" if wt_ctx["path"] else ""),
                        "task_claimed",
                        {"task_id": task.id, "worktree": task.worktree or "",
                         "auto": True},
                    )
        finally:
            if task_ctx["id"] and _task_path(task_ctx["id"]).exists():
                task = load_task(task_ctx["id"])
                if task.status == "in_progress":
                    release_result = release_task(task.id)
                    BUS.send(
                        name,
                        "lead",
                        f"Teammate exit; {release_result}",
                        "task_released",
                        {"task_id": task.id, "reason": "teammate_exit"},
                    )
                    task_ctx["id"] = None
                    wt_ctx["path"] = None

            # Summary
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
            teammate_threads.pop(name, None)
            awaiting_lead_teammates.discard(name)
            idle_teammates.discard(name)
            active_teammates.pop(name, None)
            print(f"  \033[32m[teammate] {name} finished\033[0m")

    active_teammates[name] = True
    thread = threading.Thread(target=run, daemon=True, name=f"teammate:{name}")
    teammate_threads[name] = thread
    thread.start()
    log_runtime_event("teammate_spawned", teammate=name, role=role)
    print(f"  \033[36m[teammate] {name} spawned as {role}\033[0m")
    return f"Teammate '{name}' spawned as {role} (autonomous)"


def _teammate_submit_plan(from_name: str, plan: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="plan_approval",
        sender=from_name, target="lead",
        status="pending", payload=plan)
    BUS.send(from_name, "lead", plan,
             "plan_approval_request",
             {"request_id": req_id})
    return f"Plan submitted ({req_id}). Waiting for approval..."


# ── Lead Protocol Tools (from s16) ──

def run_request_shutdown(teammate: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="shutdown",
        sender="lead", target=teammate,
        status="pending", payload="")
    BUS.send("lead", teammate, "Please shut down gracefully.",
             "shutdown_request",
             {"request_id": req_id})
    print(f"  \033[35m[protocol] shutdown_request → {teammate} "
          f"({req_id})\033[0m")
    return f"Shutdown request sent to {teammate} (req: {req_id})"


def run_request_plan(teammate: str, task: str) -> str:
    BUS.send("lead", teammate, f"Please submit a plan for: {task}",
             "message")
    return f"Asked {teammate} to submit a plan"


def run_review_plan(request_id: str, approve: bool,
                    feedback: str = "") -> str:
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    if state.status != "pending":
        return f"Request {request_id} already {state.status}"
    state.status = "approved" if approve else "rejected"
    BUS.send("lead", state.sender,
             feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_response",
             {"request_id": request_id, "approve": approve})
    icon = "✓" if approve else "✗"
    print(f"  \033[32m[protocol] plan {icon} ({request_id})\033[0m")
    return f"Plan {'approved' if approve else 'rejected'} ({request_id})"


def run_continue_teammate_work(teammate: str, guidance: str = "",
                               task_id: str = "") -> str:
    content = guidance.strip()
    if task_id:
        prefix = f"Continue working on task {task_id}."
        content = f"{prefix} {content}".strip()
    if not content:
        content = "Continue the current task and keep working until you either complete it or need to yield again."
    return send_agent_message(
        "lead",
        teammate,
        content,
        "continue_work",
        {"task_id": task_id},
    )


# ── Lead Worktree Tools (s18 new) ──

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


def run_release_task(task_id: str) -> str:
    return release_task(task_id)


def run_complete_task(task_id: str) -> str:
    return complete_task(task_id)


def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_list_teammates() -> str:
    return list_teammates()


def run_send_message(to: str, content: str) -> str:
    return send_agent_message("lead", to, content)


def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f" [{m['type']} req:{req_id}]" if req_id else f" [{m['type']}]"
        lines.append(format_labeled_block(f"  [{m['from']}]{tag} ", m["content"]))
    return "\n".join(lines)


def run_extract_key_logs(kind: str = "all", limit: int = 80) -> str:
    limit = max(10, min(int(limit), 300))
    kind = (kind or "all").strip().lower()
    allowed = {"all", "bus", "runtime"}
    if kind not in allowed:
        return f"Invalid kind '{kind}'. Use one of: all, bus, runtime."

    log_entries = []
    if kind in ("all", "bus"):
        for item in read_jsonl_tail(BUS_LOG_FILE, limit):
            log_entries.append({"source": "bus", **item})
    if kind in ("all", "runtime"):
        for item in read_jsonl_tail(RUNTIME_LOG_FILE, limit):
            log_entries.append({"source": "runtime", **item})

    log_entries = sorted(log_entries, key=lambda x: x.get("ts", 0))[-limit:]
    if not log_entries:
        return "No logs found."

    log_text = json.dumps(log_entries, ensure_ascii=False, indent=2)
    system = (
        "You extract key operational events from agent runtime logs. "
        "Return concise markdown with exactly these sections: "
        "Recent Key Events, Open Risks, Next Suggested Action. "
        "Focus on task lifecycle, teammate lifecycle, worktree disposition, "
        "errors, yields, resumes, shutdowns, and any contradictions."
    )
    user = (
        f"Summarize the most important events from these s18 logs. "
        f"kind={kind}, entries={len(log_entries)}.\n\n{log_text}"
    )
    try:
        response = client.messages.create(
            model=MODEL,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1200,
        )
    except Exception as e:
        return f"Error extracting key logs: {type(e).__name__}: {e}"

    summary = extract_text_blocks(response.content).strip() or "(no summary)"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(KEY_LOG_FILE, "a") as f:
        f.write(f"## {stamp} kind={kind} limit={limit}\n\n{summary}\n\n")
    return (f"{summary}\n\n"
            f"[saved] {KEY_LOG_FILE}")


# ── Tool Definitions ──

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
                      "properties": {
                          "command": {"type": "string"},
                          "timeout_ms": {"type": "integer"},
                          "timeout": {"type": "number"},
                      },
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
    {"name": "create_task",
     "description": "Create a task.",
     "input_schema": {"type": "object",
                      "properties": {"subject": {"type": "string"},
                                     "description": {"type": "string"},
                                     "blockedBy": {"type": "array",
                                                   "items": {"type": "string"}}},
                      "required": ["subject"]}},
    {"name": "list_tasks",
     "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_task",
     "description": "Get full details of a specific task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "claim_task",
     "description": "Claim a pending task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "release_task",
     "description": "Release an in-progress task back to pending.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "complete_task",
     "description": "Complete an in-progress task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "spawn_teammate",
     "description": "Spawn an autonomous teammate agent.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "role": {"type": "string"},
                                     "prompt": {"type": "string"}},
                      "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates",
     "description": "List known teammates and whether they are active, idle, or offline.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "send_message",
     "description": "Send message to a teammate.",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["to", "content"]}},
    {"name": "check_inbox",
     "description": "Check inbox for messages and protocol responses.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "request_shutdown",
     "description": "Request a teammate to shut down gracefully.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"}},
                      "required": ["teammate"]}},
    {"name": "request_plan",
     "description": "Ask a teammate to submit a plan for review.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"},
                                     "task": {"type": "string"}},
                      "required": ["teammate", "task"]}},
    {"name": "review_plan",
     "description": "Approve or reject a submitted plan.",
     "input_schema": {"type": "object",
                      "properties": {
                          "request_id": {"type": "string"},
                          "approve": {"type": "boolean"},
                          "feedback": {"type": "string"}},
                      "required": ["request_id", "approve"]}},
    {"name": "continue_teammate_work",
     "description": "Tell a teammate holding an in-progress task to continue working.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"},
                                     "guidance": {"type": "string"},
                                     "task_id": {"type": "string"}},
                      "required": ["teammate"]}},
    # s18 new: worktree tools
    {"name": "create_worktree",
     "description": "Create an isolated git worktree with its own branch.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "task_id": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "remove_worktree",
     "description": "Remove a worktree. Refuses if uncommitted changes unless discard_changes=true.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "discard_changes": {"type": "boolean"}},
                      "required": ["name"]}},
    {"name": "keep_worktree",
     "description": "Keep a worktree for manual review.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
]

TOOL_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "create_task": run_create_task, "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task, "release_task": run_release_task,
    "complete_task": run_complete_task,
    "spawn_teammate": run_spawn_teammate,
    "list_teammates": run_list_teammates,
    "send_message": run_send_message, "check_inbox": run_check_inbox,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
    "continue_teammate_work": run_continue_teammate_work,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
}


# ── Context ──

MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"


def update_context(context: dict, messages: list) -> dict:
    memories = ""
    if MEMORY_INDEX.exists():
        memories = MEMORY_INDEX.read_text()[:2000]
    return {"memories": memories}


# ── Agent Loop ──

def agent_loop(messages: list, context: dict):
    system = get_system_prompt(context)
    while True:
        try:
            response = client.messages.create(
                model=MODEL, system=system, messages=messages,
                tools=TOOLS, max_tokens=8000)
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
            handler = TOOL_HANDLERS.get(block.name)
            output = invoke_handler(handler, block.input) if handler else "Unknown"
            print(str(output)[:300])
            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
        context = update_context(context, messages)
        system = get_system_prompt(context)


if __name__ == "__main__":
    print("s18: worktree isolation")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    history = []
    context = {"memories": ""}
    while True:
        try:
            query = input("\033[36ms18 >> \033[0m")
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

        # Consume lead inbox: route protocol + inject into history
        inbox = consume_lead_inbox(route_protocol=True)
        if inbox:
            inbox_text = "\n".join(
                f"From {m['from']} [{m.get('type', 'message')}]: "
                f"{m['content'][:200]}" for m in inbox)
            history.append({"role": "user",
                            "content": f"[Inbox]\n{inbox_text}"})
        print()
