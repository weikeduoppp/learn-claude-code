# Docs and Configuration Review

## Documentation reviewed
- `README.md`
- `CONTRIBUTING.md`
- `web/README.md`

## Configuration reviewed
- `.env.example`
- `requirements.txt`
- `web/package.json`
- `frontend/package.json`

## Findings

### 1. Project documentation and contribution policy
- `README.md` clearly positions the repository as a tutorial on **agent harness engineering**.
- It explicitly distinguishes:
  - the **current canonical 20-lesson track** in root-level `s01_*` to `s20_*`
  - the **legacy transition track** in `agents/`, `docs/`, and the current `web/` app
- `CONTRIBUTING.md` emphasizes that this is a **curated teaching repository**, so contributions should:
  - be tied to a specific issue
  - preserve minimal teaching-oriented code
  - keep all three chapter languages in sync
  - target current course files, not legacy mirrors
  - disclose AI assistance

### 2. Python/runtime configuration
- `requirements.txt` is intentionally small:
  - `anthropic>=0.25.0`
  - `python-dotenv>=1.0.0`
  - `pyyaml>=6.0`
- `.env.example` documents required runtime variables:
  - `ANTHROPIC_API_KEY`
  - `MODEL_ID`
- Optional configuration:
  - `ANTHROPIC_BASE_URL` for Anthropic-compatible providers
- The file includes extensive examples for multiple providers (Anthropic, MiniMax, GLM, Kimi, DeepSeek), including international and mainland-China endpoints.
- This suggests the codebase is designed to be provider-flexible as long as the provider supports an Anthropic-compatible API.

### 3. Web app configuration
#### `web/`
- `web/package.json` defines a **Next.js** app.
- Key scripts:
  - `extract`: content extraction script via `tsx`
  - `predev` and `prebuild`: run extraction before dev/build
  - `dev`, `build`, `start`: standard Next.js lifecycle
- Main dependencies include:
  - `next`, `react`, `react-dom`
  - markdown/rehype/remark processing stack
  - `framer-motion`, `lucide-react`, `diff`
- Interpretation:
  - the web app likely transforms repository docs into a rendered documentation experience.
  - the extraction step is important to the web build pipeline.

#### `frontend/`
- `frontend/package.json` defines a smaller **Vite + React** frontend.
- Scripts are standard `vite` dev/build/preview commands.
- This appears to be separate from the main `web/` Next.js app, likely for a landing page or alternate frontend experiment.

### 4. Documentation mismatch / note
- `README.md` says the current web app renders the legacy `docs/` track.
- `web/README.md` is still the generic default `create-next-app` README and does **not** describe the actual repository-specific extraction/build behavior.
- This is not necessarily a functional bug, but it is a documentation gap.

## Key configuration conclusions
- Main Python execution depends on environment variables and Anthropic-compatible APIs.
- The current course content is in root lesson folders, not in `agents/` or `docs/`.
- The `web/` application has a content extraction preprocessing step that appears essential.
- `frontend/` and `web/` are distinct frontend surfaces with different stacks.

## Suggested improvement
If documentation polish is desired, the highest-value docs/config improvement would be:
- replace or expand `web/README.md` so it explains:
  - what content it renders
  - what `npm run extract` does
  - whether it targets legacy or current course content
  - how it relates to the separate `frontend/` app
