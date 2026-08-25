# Repository Structure Inspection

## Overview
This repository is a tutorial/project about building agent harnesses, centered on progressive lessons and legacy runnable agent implementations.

## Top-level structure
- `README.md`, `README-zh.md`, `README-ja.md`: main documentation and onboarding for the project.
- `requirements.txt`: minimal Python dependencies (`anthropic`, `python-dotenv`, `pyyaml`).
- `agents/`: legacy 12-lesson runnable agent scripts plus `s_full.py` reference implementation.
- `s01_agent_loop/` ... `s20_comprehensive/`: current canonical 20-lesson tutorial track. Each lesson generally contains:
  - `README.md`, `README.en.md`, `README.ja.md`
  - `code.py`
  - `images/` for diagrams where applicable
- `skills/`: skill packs such as `agent-builder`, `code-review`, `mcp-builder`, `pdf`.
- `tests/`: Python tests for smoke checks and selected features.
- `docs/`: legacy docs track still kept for compatibility.
- `web/`: Next.js-based web app that currently renders the legacy docs track.
- `frontend/`: additional frontend/Vite app assets.
- `.tasks/`, `.mailboxes/`, `.memory/`: runtime/project data for task board, agent communication, and memory.

## Key Python modules
### Current lesson modules
The root lesson folders are the main learning path:
- `s01_agent_loop/code.py`: minimal agent loop
- `s02_tool_use/code.py`: tool dispatch expansion
- `s03_permission/code.py`: permission boundaries
- `s04_hooks/code.py`: hook system
- `s05_todo_write/code.py`: planning/todo management
- `s06_subagent/code.py`: subagent delegation
- `s07_skill_loading/code.py`: skill loading
- `s08_context_compact/code.py`: context compression/compaction
- `s09_memory/code.py`: memory system
- `s10_system_prompt/code.py`: runtime prompt assembly
- `s11_error_recovery/code.py`: retry/error recovery
- `s12_task_system/code.py`: persistent task graph
- `s13_background_tasks/code.py`: background jobs
- `s14_cron_scheduler/code.py`: scheduling
- `s15_agent_teams/code.py`: team coordination
- `s16_team_protocols/code.py`: protocol rules
- `s17_autonomous_agents/code.py`: self-claiming autonomous agents
- `s18_worktree_isolation/code.py`: task-directory isolation
- `s19_mcp_plugin/code.py`: MCP plugin/tool integration
- `s20_comprehensive/code.py`: full combined harness

### Legacy runnable modules in `agents/`
- `agents/s01_agent_loop.py` through `agents/s12_worktree_task_isolation.py`: older 12-lesson runnable copies
- `agents/s_full.py`: capstone/reference implementation combining major mechanisms from the legacy track

## Important supporting areas
- `tests/test_agents_smoke.py`: compiles all legacy `agents/*.py` scripts as a smoke test
- `tests/test_compaction_tool_pairs.py`, `tests/test_s_full_background.py`, `tests/test_todo_write_string_input.py`: targeted regression/behavior checks
- `docs/en`, `docs/ja`, `docs/zh`: multilingual documentation content
- `web/src`, `web/public`: web application source and assets

## Observations
- The repository currently contains both a new 20-lesson canonical track and an older legacy track maintained for compatibility.
- The most important modules for readers starting fresh are the root-level `s01_*` through `s20_*` folders.
- The main integrated reference implementation on the legacy side is `agents/s_full.py`, which includes tools, todo management, subagents, skill loading, compression, tasks, background jobs, messaging, team management, and the central agent loop.
