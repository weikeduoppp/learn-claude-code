# Quick Improvement Opportunities

## High-confidence, low-risk improvements

1. **Make root validation commands explicit**
   - `README.md` has a good Quick Start section, but it still does not give a compact contributor validation checklist.
   - Suggested addition near `README.md:352`:
     - create venv
     - install `requirements.txt`
     - run `python -m pytest` (preferred over bare `pytest`)
     - build `frontend/`
     - build `web/`
   - Reason: `pytest` was not available as a global shell command in my environment, so `python -m pytest` is the safer documented form once installed.

2. **Replace generic `web/README.md`**
   - Current file is stock Next.js boilerplate and does not explain the repo-specific `extract`, `predev`, and `prebuild` steps.
   - This is probably the clearest documentation quick win in the repo.

3. **Document what the web app actually renders**
   - Root README mentions that `web/` still renders the legacy `docs/` track.
   - `web/README.md` should repeat this clearly to prevent confusion for contributors who enter through that subdirectory.

4. **Add a short “expected local artifacts” note**
   - Helpful to mention `.venv/`, `node_modules/`, and generated lock/install artifacts as expected local setup state that should not be committed casually.

5. **Clarify contributor success criteria**
   - `CONTRIBUTING.md` is strong on contribution philosophy, but light on how contributors should sanity-check changes before opening a PR.
   - A tiny “Before submitting, run …” section would reduce ambiguity.

## Supporting observations

- Root Python dependencies are documented in `requirements.txt`.
- `frontend/package.json` exposes `dev`, `build`, `preview`.
- `web/package.json` exposes `extract`, `dev`, `build`, and uses `predev`/`prebuild` hooks.
- `web/README.md` currently does not mention any of those repo-specific details.

## Recommended first two changes

1. Rewrite `web/README.md` with repo-specific setup and architecture notes.
2. Add a short root README contributor validation block using `python -m pytest` and the two frontend build commands.
