# How to run it

## Requirements

- Python 3.11, 3.12 or 3.13
- Node.js 20 or later, only for the React frontend
- No API key is needed to try it. Without one, it runs on a free offline mock LLM.

## 1. Install

```bash
git clone https://github.com/anirudhyadav-ai-a/JAGGAERConcierge.git
cd JAGGAERConcierge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run offline (free)

```bash
make test          # the full test suite, offline
python demo.py     # runs the demo questions and prints route, tools, LLM calls and cost
make serve         # web app on http://localhost:8000 (API docs at /docs)
```

Nothing here calls a paid API. `make test` forces `LLM_PROVIDER=mock`, and with no key set the default `auto`
picks the mock too.

## 3. Run with the React frontend

The backend and the React app run as two servers in development:

```bash
make frontend-install   # once: installs the frontend's npm packages (cd frontend && npm ci)
make dev                # backend on :8000 and the React dev server on :5173, together
```

Open **http://localhost:5173**. The React dev server passes every `/v1/...` and `/health` call through to the
backend on port 8000, so there's nothing else to configure. Ctrl+C stops both servers. Edits to files in
`frontend/src` show up in the browser straight away.

To run them in two terminals instead:

```bash
make serve              # terminal 1: backend on :8000
make frontend-dev       # terminal 2: React on :5173
```

If the backend runs somewhere else, point the dev server at it: `cd frontend && API_TARGET=http://host:8000 npm run dev`.

### One server instead of two

Build the React app once and the backend serves it itself:

```bash
make frontend-build     # writes frontend/dist/
make serve
```

Then open **http://localhost:8000/app/**. The original single-file page stays at http://localhost:8000/. Rebuild
after changing the frontend. If `frontend/dist/` doesn't exist, `/app/` explains how to build it.

| URL | What it is |
| --- | --- |
| http://localhost:5173 | React app, development server (`make dev`) |
| http://localhost:8000/app/ | React app, built and served by the backend (`make frontend-build`) |
| http://localhost:8000/ | the original single-file page |
| http://localhost:8000/docs | interactive API docs |

Both pages talk to the same API and behave the same way.

## 4. Run on Claude (paid)

Read [cost.md](cost.md) first and set a spend limit in the Claude Console before you add a key.

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
python demo.py                                   # the demo questions, now on Claude
python demo.py "Do I have any pending charges?"  # your own question
make serve                                       # the web app, on Claude
```

With a key present, `LLM_PROVIDER=auto` switches to Claude with no code changes. `.env` is git-ignored; never
commit it. `demo.py` prints the total spent at the end.

To force a provider either way:

```bash
LLM_PROVIDER=mock python demo.py         # free, even with a key in .env
LLM_PROVIDER=anthropic python demo.py    # fails at start-up if ANTHROPIC_API_KEY is missing
```

## Settings

All settings are environment variables (or lines in `.env`). `.env.example` lists the common ones.

| Variable | Default | What it does |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Claude API key. Never logged. |
| `LLM_PROVIDER` | `auto` | `auto` (Claude if a key is set, else mock), `anthropic` or `mock` |
| `INTENT_MODEL` | `claude-haiku-5-5` | model for routing |
| `AGENT_MODEL` | `claude-sonnet-5-5` | model for agents |
| `COLLATE_MODEL` | `claude-sonnet-5-5` | model for the final combined answer |
| `LLM_MAX_TOKENS` | `2048` | max output tokens per call |
| `LLM_TIMEOUT_S` | `60` | timeout per API call |
| `BUDGET_USD` | `5.0` | in-app spend cap (see [cost.md](cost.md)) |
| `MAX_LLM_CALLS_PER_REQUEST` | `12` | cap on LLM calls for one question |
| `USAGE_LEDGER_PATH` | `.usage/ledger.json` | where actual spend is recorded |
| `INTENT_THRESHOLD` | `0.55` | minimum routing score for an agent to run |
| `HISTORY_TURNS` | `4` | session turns the router sees |
| `MAX_ORCHESTRATOR_LOOPS` | `3` | collator loop-backs per request |
| `MAX_TASK_ATTEMPTS` | `2` | attempts per agent task before it is reported as failed |
| `AGENT_TIMEOUT_S` | `90` | timeout per agent task |
| `AGENT_MAX_TOOL_STEPS` | `4` | tool rounds per agent |
| `DATA_DIR` | `./data` | folder the agents' data is read from |
| `TODAY` | `2026-11-27` | pinned "today", so "this month" is stable |
| `AUDIT_PATH` | `audit.jsonl` | audit log file |

## Make targets

| Command | Does |
| --- | --- |
| `make install` | `pip install -r requirements.txt` |
| `make test` | runs the test suite on the mock LLM |
| `make demo` | `python demo.py` |
| `make serve` | starts the backend with auto-reload on port 8000 |
| `make frontend-install` | installs the React app's packages |
| `make dev` | backend on :8000 and the React dev server on :5173 |
| `make frontend-dev` | the React dev server alone |
| `make frontend-build` | builds the React app into `frontend/dist/` (served at `/app/`) |
| `make frontend-test` | runs the React app's tests |

## Continuous integration

`.github/workflows/ci.yml` runs the tests and `demo.py` on Python 3.11, 3.12 and 3.13 on every push and pull
request, always on the mock, so CI never spends money. A second job installs the React app, runs its tests and
builds it.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set` | add the key to `.env`, or use `LLM_PROVIDER=mock` |
| Answer says the key was rejected | the key is wrong or revoked; check `.env` |
| Answer says rate or spend limit reached | wait a moment, or check the workspace spend limit in the Console |
| Status `budget_exceeded` | the in-app cap was hit; see [cost.md](cost.md) to check or reset it |
| `data source ... expects .../data/...` | `DATA_DIR` points at the wrong folder |
| Port 8000 in use | `python -m uvicorn app.main:app --port 8001`, and start the React app with `API_TARGET=http://localhost:8001` |
| React page says "server not reachable" | the backend isn't running on :8000; start it with `make serve` and reload |
| `/app/` returns "frontend isn't built" | run `make frontend-build`, then restart `make serve` |
| `npm: command not found` | install Node.js 20 or later |
