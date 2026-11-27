# How to run it

## Requirements

- Python 3.11, 3.12 or 3.13
- Node.js 20 or later, for the React frontend
- A Claude API key. The app answers every question with real Claude calls: Haiku routes, Sonnet runs the agents
  and writes the answer. Read [cost.md](cost.md) first; a full two-agent question costs about one to three cents.

## 1. Install and add your key

```bash
git clone https://github.com/anirudhyadav-ai-a/JAGGAERConcierge.git
cd JAGGAERConcierge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set ANTHROPIC_API_KEY=sk-ant-... in .env
```

Create the key in a Claude Console workspace with a monthly spend limit (see [cost.md](cost.md)). `.env` is
git-ignored; never commit it. Without a key, the app stops at start-up and says which variable to set.

## 2. Run it

```bash
python demo.py                                   # the four demo questions, on Claude
python demo.py "Do I have any pending charges?"  # your own question
make serve                                       # backend + original page on http://localhost:8000
```

`demo.py` prints each question's route, agents, tools, LLM calls and cost, then the total spent.

To confirm everything works on Claude, run the live check. It asks the four interview questions and checks each
answer for the facts the spec requires (amounts, the dispute case id, the policy citation, the recommended card)
and for the right agents and tools. It costs roughly $0.03–0.10 and stops itself at $0.30:

```bash
make live-check          # or: python scripts/live_check.py --max-usd 0.30
```

## 3. Run with the React frontend

The backend and the React app run as two servers in development:

```bash
make frontend-install   # once: installs the frontend's npm packages (cd frontend && npm ci)
make dev                # backend on :8000 and the React dev server on :5173, together
```

Open **http://localhost:5173**. The React dev server passes every `/v1/...` and `/health` call through to the
backend on port 8000, so there's nothing else to configure. The header shows "Claude" and the budget left. Ctrl+C
stops both servers. Edits to files in `frontend/src` show up in the browser straight away.

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

## 4. Tests (no API key, no cost)

```bash
make test               # backend tests
make frontend-test      # React tests
```

The tests never call the paid API. They run the real pipeline against a deterministic stand-in LLM
(`LLM_PROVIDER=mock`) and a fake Claude client that returns genuine SDK response objects and enforces the API's
model rules. That's what lets CI run on every push for free. The mock exists only for this; the app itself always
uses Claude.

## 5. Load benchmark (no API key, no cost)

```bash
python bench.py                               # 200 concurrent requests, rows 2-5, cache off vs on
```

It uses the test mock with 400 ms per simulated LLM call. Last run: with the cache on, 80% of requests were cache
hits, average LLM calls per request fell from 5.6 to 2.4, median latency from 1.7 s to 1.0 s, and throughput rose
from 79 to 113 requests per second.

## 6. Scale check (no API key, no cost)

```bash
python scripts/scale_check.py                 # 1,000,000 transactions across 10,000 customers
```

It generates the data in a temporary folder and times the agents' tools on it. On a laptop-class machine, each
tool call takes about 0.1 ms at a million rows, and at most 20 rows ever reach the LLM, so a question costs the
same whatever the data size. See [architecture.md](architecture.md#scaling-the-data) for what changes beyond one
machine's memory.

## Settings

All settings are environment variables (or lines in `.env`). `.env.example` lists the common ones.

| Variable | Default | What it does |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none (required) | Claude API key. Never logged. |
| `LLM_PROVIDER` | `anthropic` | `anthropic` (real Claude calls). `mock` is for the test suite and CI only |
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
| `CACHE_ENABLED` | `true` | plan cache on or off |
| `CACHE_PATH` | `.cache/plans.json` | where saved plans are kept; empty keeps them in memory only |
| `CACHE_TTL_S` | `86400` | how long a plan stays usable |
| `CACHE_SIMILARITY` | `0.80` | how similar a question must be to reuse a plan |
| `CACHE_MAX_NEGATIVE` | `2` | "more details" requests after which a plan is no longer used |
| `MOCK_LLM_LATENCY_MS` | `0` | simulated latency per call for the test mock (`bench.py` sets 400) |

## Make targets

| Command | Does |
| --- | --- |
| `make install` | `pip install -r requirements.txt` |
| `make test` | runs the backend tests (no API calls) |
| `make demo` | `python demo.py` |
| `make serve` | starts the backend with auto-reload on port 8000 |
| `make frontend-install` | installs the React app's packages |
| `make dev` | backend on :8000 and the React dev server on :5173 |
| `make frontend-dev` | the React dev server alone |
| `make frontend-build` | builds the React app into `frontend/dist/` (served at `/app/`) |
| `make frontend-test` | runs the React app's tests |
| `make live-check` | asks the interview questions on real Claude and checks the answers (costs a few cents) |
| `make scale-check` | times the tools on a million generated transactions (free) |
| `make bench` | 200 concurrent requests with the cache off and on (free) |

## Continuous integration

`.github/workflows/ci.yml` runs the tests and `demo.py` on Python 3.11, 3.12 and 3.13 on every push and pull
request, always on the mock, so CI never spends money. A second job installs the React app, runs its tests and
builds it.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot start: ANTHROPIC_API_KEY is not set` | copy `.env.example` to `.env` and put your key in it |
| Answer says "Claude declined to answer" | the model refused that question; rephrase it |
| Answer says the key was rejected | the key is wrong or revoked; check `.env` |
| Answer says rate or spend limit reached | wait a moment, or check the workspace spend limit in the Console |
| Status `budget_exceeded` | the in-app cap was hit; see [cost.md](cost.md) to check or reset it |
| `data source ... expects .../data/...` | `DATA_DIR` points at the wrong folder |
| Port 8000 in use | `python -m uvicorn app.main:app --port 8001`, and start the React app with `API_TARGET=http://localhost:8001` |
| React page says "server not reachable" | the backend isn't running on :8000; start it with `make serve` and reload |
| `/app/` returns "frontend isn't built" | run `make frontend-build`, then restart `make serve` |
| `npm: command not found` | install Node.js 20 or later |
