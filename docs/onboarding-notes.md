# Onboarding Notes and Documentation Gaps

## What currently works

- Root Python environment can be set up with:
  - `python3 -m venv .venv`
  - `source .venv/bin/activate`
  - `pip install -r requirements.txt`
- `frontend/` installs successfully with `npm install`
- `web/` installs successfully with `npm install`

## Observed documentation gaps

1. **No single quick-start section at the repo root**
   - The root `README.md` explains the project well, but does not provide a concise setup + validation checklist for contributors.
   - A new contributor has to infer that there are multiple runnable surfaces: root Python lessons, `frontend/`, and `web/`.

2. **Validation commands are not obvious**
   - There is no short root-level section that answers:
     - how to run tests
     - whether both `frontend` and `web` should be built or started
     - what constitutes a healthy local setup

3. **`web/README.md` is still generic Next.js boilerplate**
   - It does not explain how `web/` relates to this repository.
   - It does not mention the `extract` / `predev` / `prebuild` flow that exists in `web/package.json`.

4. **Install-script warnings may confuse contributors**
   - `npm install` in `frontend/` and `web/` produced non-blocking install-script warnings.
   - Even a brief note in contributor docs would help distinguish warnings from actual setup failures.

5. **Local artifact expectations are undocumented**
   - Repo-local setup creates `.venv/` and package lock/install state.
   - It would help to state which generated local artifacts are expected and should remain uncommitted.

## Quick-win recommendations

- Add a root-level "Quick start" section to `README.md`
- Add a root-level "Validation" section with exact commands
- Replace `web/README.md` boilerplate with repo-specific instructions
- Mention that some package managers may emit install-script warnings that are informational unless they break build/dev commands

## Suggested validation commands

```bash
# root python env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# python tests
pytest

# frontend
cd frontend && npm install && npm run build

# web
cd web && npm install && npm run build
```
