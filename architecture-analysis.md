# Codebase Architecture Analysis

## High-level purpose
This repository is a tutorial and reference project for building agent harnesses around LLMs. It contains:
- a **current canonical 20-lesson track** in root-level `s01_*` to `s20_*`
- a **legacy 12-lesson track** in `docs/`, `agents/`, and the current `web/` app
- lightweight tests, example frontend apps, and reusable skills

The architecture is content-first: most directories are self-contained lesson modules pairing narrative docs, runnable code, and diagrams.

## Top-level architectural layers

### 1. Learning content layer
Primary user-facing learning materials live in the root lesson directories:
- `s01_agent_loop/` ... `s20_comprehensive/`
- each lesson usually includes:
  - `README.md`, `README.en.md`, `README.ja.md`
  - `code.py`
  - `images/` diagrams
  - sometimes `example/` or learning notes in Chinese

This is the **main product surface** for new readers.

### 2. Legacy implementation layer
Older tutorial/reference materials remain for compatibility:
- `agents/` contains legacy Python agent examples (`s01`-`s12`, plus `s_full.py`)
- `docs/zh`, `docs/en`, `docs/ja` contain the older 12-lesson markdown track
- `web/` appears to present the legacy track through a Next.js site

This layer is explicitly described in `README.md` as transitional.

### 3. Supporting assets and examples
- `skills/`: reusable skill documents (`SKILL.md`) loaded by lessons/reference agents
- `tests/`: smoke and focused regression tests
- `frontend/`: a separate small Vite/React tutorial homepage
- standalone demo/reference files like `schema.sql`, `schema_v2.sql`, and generated large test fixtures

## Key modules and responsibilities

### Root lesson directories (`s01_*` ... `s20_*`)
These are **vertical slices** rather than shared libraries.

Common pattern per lesson:
- `code.py`: runnable chapter implementation
- README variants: explanation and pedagogy
- diagrams: architecture visuals for the lesson concept

Architecturally, each lesson is intentionally semi-duplicated to keep chapters self-contained for learners, rather than extracting all shared code into a library.

### `agents/`
This directory acts as a **legacy reference implementation set**.
- each file demonstrates a harness milestone from the older curriculum
- `agents/s_full.py` is the capstone “full reference agent” that combines mechanisms

`agents/s_full.py` shows the repository’s conceptual target architecture:
- base tools: shell and file operations
- todo management
- subagents
- skill loading
- context compaction
- background tasks
- team communication and inboxes
- task claiming/planning/shutdown flows

This file is the closest thing to a monolithic reference architecture in the repo.

### `docs/`
This is the **legacy multilingual documentation track**, split by language:
- `docs/en`
- `docs/zh`
- `docs/ja`

The structure suggests a static-content architecture where lessons are duplicated per locale rather than generated from a single source.

### `web/`
A **Next.js web application** for presenting content.
Observed structure suggests:
- static/exported output under `web/out`
- build artifacts under `.next`
- source/config under standard Next.js files

Because checked-in `.next/` and `out/` artifacts are present, this app currently behaves partly like a generated/static deployment workspace, not a clean source-only app.

### `frontend/`
A smaller **Vite + React** frontend, likely a simpler homepage or alternate presentation layer.
This introduces architectural duplication in UI delivery:
- `web/`: Next.js legacy platform
- `frontend/`: Vite-based homepage/app

This is a notable repo complexity point.

### `tests/`
Current tests are lightweight and pragmatic:
- compile/smoke checks for agent scripts
- behavior-specific tests for compaction/background/todo pieces

The test architecture is focused on **guardrails for examples/reference code**, not a deeply modular unit-test suite.

## Architectural style

### Primary style: pedagogical modular monorepo
This is not a conventional package-oriented Python application. It is better understood as a **monorepo of tutorial modules and reference artifacts**.

Characteristics:
- chapter-oriented organization
- self-contained examples over shared abstractions
- multilingual docs duplicated near lesson content
- legacy and current tracks coexisting

### Secondary style: reference harness patterns
Across lessons and legacy agents, the conceptual software architecture is consistent:
1. model loop
2. tool dispatch
3. permissions/hooks/planning
4. subagents and context management
5. tasks/background execution/team coordination
6. optional UI/documentation surfaces

So the codebase teaches an architecture while also structurally embodying it.

## Important architectural boundaries

### Boundary A: canonical vs legacy track
Clear separation exists between:
- **canonical current lessons**: root `s01_*` ... `s20_*`
- **legacy track**: `agents/`, `docs/`, `web/`

This is the most important repo-level boundary for contributors.

### Boundary B: tutorial code vs production-like reference
- lesson `code.py` files optimize for clarity
- `agents/s_full.py` is closer to a consolidated reference implementation

Contributors should avoid over-generalizing lesson code at the expense of readability.

### Boundary C: content vs app presentation
- lesson directories and docs are source content
- `web/` and `frontend/` are presentation shells

This means documentation changes and UI changes can evolve somewhat independently.

## Likely contributor workflow
A contributor typically works in one of four areas:
1. **lesson authoring**: update a chapter’s README/code/images
2. **legacy compatibility**: adjust `agents/` or `docs/` content
3. **web presentation**: update `web/` or `frontend/`
4. **test guardrails**: add/maintain smoke coverage in `tests/`

## Notable architectural strengths
- Very clear chapter-based discoverability
- Canonical-vs-legacy status is documented in README
- Strong self-contained lesson packaging
- Multilingual documentation built into repo structure
- Reference capstone agent available for end-to-end understanding

## Notable architectural risks / complexity points
- Two frontend stacks (`web` and `frontend`) increase maintenance overhead
- Legacy and canonical tracks can drift
- Checked-in build artifacts under `web/.next` and `web/out` blur source vs generated output
- Repetition across lessons is useful pedagogically but raises update cost

## Suggested mental model
Think of the repository as three concentric rings:

1. **Core educational content**
   - `s01_*` ... `s20_*`
2. **Legacy/reference compatibility ring**
   - `agents/`, `docs/`, `web/`
3. **Supporting utilities and examples**
   - `tests/`, `skills/`, `frontend/`, fixture files

## Summary
The repository architecture is primarily a **tutorial monorepo for agent harness engineering**, centered on root-level lesson modules. The most important organizational fact is the coexistence of a newer canonical 20-lesson track and an older legacy 12-lesson track. Rather than optimizing for reusable package structure, the repo optimizes for teaching clarity, multilingual documentation, and runnable examples, with `agents/s_full.py` serving as the most complete consolidated architectural reference.
