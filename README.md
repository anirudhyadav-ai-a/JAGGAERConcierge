# Agent Orchestration Platform

A multi-agent RAG platform for a banking assistant. A customer asks one question; the platform works out
which domain agents are needed, each agent uses only its own tools, and the customer gets one answer.
LLMs read the question and write the answers. Routing, planning, execution order and retries are plain code.

## Documentation

| Doc | Covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | request flow, agents, tools and data sources, LLM layer, code map |
| [docs/running.md](docs/running.md) | install, run offline or on Claude, every setting, troubleshooting |
| [docs/usage.md](docs/usage.md) | the comparison page, questions to try, the HTTP API and its responses |
| [docs/cost.md](docs/cost.md) | prices, what a question costs, the two spend limits, checking and reducing spend |

## Quickstart (no API key needed)

```bash
git clone <this repo> && cd JAGGAERConcierge
python3 -m pip install -r requirements.txt     # Python 3.11+
python3 demo.py                                # runs the scenarios on the free offline mock LLM
python3 -m pytest -q                           # runs the full suite offline
```

## See before vs after in the browser

```bash
make serve                    # then open http://localhost:8000
```

Ask one question and it goes to both sides. On the left (before) you tick the agents; on the right (after) the
platform picks them. Everything after that choice is the same pipeline, so when you tick the agents the question
really needs, both sides run the same agents and tools and give the same answer. A strip under the question
compares the two. Pick the wrong agents, or ask something ambiguous, and it shows where they differ. Each answer
shows the agents, tools, LLM calls, cost, time and a link to its audit trail.

## Run it with Claude

```bash
cp .env.example .env          # then put your key in ANTHROPIC_API_KEY
python3 demo.py               # now uses Claude: Haiku 5.5 routes, Sonnet 5.5 runs agents and writes answers
python3 demo.py "Do I have any pending charges?"
```

`.env` is git-ignored. Never commit a key.

### Spending protection (two layers)

1. **Console (authoritative).** In the Claude Console, create a dedicated workspace, set its monthly spend
   limit to $5, and create the API key *inside that workspace*. The key can't spend beyond the limit.
2. **In the app.** `BUDGET_USD` (default 5.0) is enforced before every call. The app reserves each call's
   worst-case cost (input estimate + `max_tokens` of output) and refuses the call if it could cross the
   budget, so spend never exceeds it. Actual cost is recorded from each response's `usage` block in
   `.usage/ledger.json`, which survives restarts. `MAX_LLM_CALLS_PER_REQUEST` (default 12) caps any single request.
   Check spend at `GET /v1/usage`.

The in-app ledger is per machine, so layer 1 is what protects a key shared with someone else.

Rough cost on Claude: about $0.0002 for an ambiguous question, and $0.01–0.03 for a two-agent question.
See [docs/cost.md](docs/cost.md).

## Demo scenarios

| Case | Question | What happens |
| --- | --- | --- |
| Ambiguous | What about my card? | Router flags ambiguity; the user gets a clarifying question; **no agent runs** (1 LLM call) |
| Single agent, 2 tools | What's happening with my $89.99 Online Store 24 charge, and where is the dispute? | A12 calls `search_transactions`, then `get_dispute_status` using the id it found |
| Multi-agent, 1 tool each | What is the late payment fee, and how much did I spend on dining this month? | A13 `search_policies` and A14 `spending_summary(category=dining)` run **in parallel**; one combined answer |
| Dependent agents | How much did I spend this month and which credit card suits my spending? | A14 runs first; A11 uses its output to recommend cards |

Each result prints the route, agents, tools, number of LLM calls, cost and the audit trace id.

## Architecture

```
Client + session
  -> Intent pre-processing   (LLM: route_intents tool -> agents, sub-queries, dependencies, ambiguity)
  -> Intent score            (rules: accept / clarify / out of scope; clarify answers without running agents)
  -> Orchestration graph
       Orchestrator          (task DAG from intents; on loop-back: retries, skips blocked tasks)
       Executor              (runs ready tasks in parallel waves, timeout per agent)
       Agents A11-A14        (LLM tool loop over their OWN tools only)
       Response collator     (loops back while tasks wait; otherwise writes the final answer)
  -> Final response + Audit log (every stage, one trace_id, PII redacted)
```

| Agent | Domain | Tools |
| --- | --- | --- |
| A11 | Credit card recommendation | `get_customer_profile`, `recommend_cards` |
| A12 | Transactions | `search_transactions`, `get_dispute_status` |
| A13 | Policy knowledge base (RAG) | `search_policies` (cites policy ids) |
| A14 | Personal finance | `spending_summary`, `budget_status` |

Design rules:

- **No guessing.** When routing is ambiguous, the user is asked; the router uses a schema-validated tool, not free text.
- **Agent isolation.** An agent can call only its own tools. `customer_id` is injected by the platform, never
  chosen by the LLM, and tools reject unknown arguments.
- **Graceful failure.** Agent errors and timeouts are retried, then reported as `partial`, never a crash.

### LLM calls per request

| Path | Calls |
| --- | --- |
| Ambiguous / out of scope | 1 |
| One agent | 1 + 2-4 (one per tool round + the answer) |
| N agents | 1 + 2-4 per agent + 1 collate |

### Claude-specific handling (`app/llm/anthropic_llm.py`)

- Sonnet 5.5 runs adaptive thinking by default and rejects `thinking: disabled` and forced tool choice. The
  provider sends `between_tools` and lets the model choose tools.
- Haiku 5.5 accepts both, so the router forces `route_intents` with thinking off, which is fast and cheap.
- Thinking blocks are passed back unchanged in tool loops, as the API requires.

## API

```bash
make serve     # uvicorn on :8000, docs at /docs
curl -s localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"customer_id":"C1001","message":"What about my card?"}'
```

`POST /v1/chat` · `POST /v1/chat/manual` (before mode: same pipeline, user-chosen `agent_ids`) · `GET /v1/audit/{trace_id}` · `GET /v1/sessions/{id}` · `GET /v1/agents` · `GET /v1/usage` · `GET /health`

In the demo, `customer_id` comes in the request body. In production it comes from the auth token.

## Tests

`make test` runs the whole suite offline:

- the scenarios above
- customer isolation
- retries, timeouts and skipped dependents
- audit redaction
- the API

`tests/test_claude_integration.py` drives the real Claude provider through a fake SDK client that returns genuine
SDK response objects and enforces the API's model rules. That covers model selection, thinking replay, cost
accounting and the budget stop without spending money.

## Data sources

All demo data lives in files under `data/`, and each agent reads only the sources it declares:

| Agent | Sources | Files |
| --- | --- | --- |
| A11 Credit Card Recommendation | customers, card_products, transactions | `customers.json`, `card_products.json`, `transactions.csv` |
| A12 Transaction Queries | transactions, disputes | `transactions.csv`, `disputes.json` |
| A13 Policy Knowledge Base | policies | `policies/*.md` |
| A14 Personal Finance (PFM) | transactions, customers | `transactions.csv`, `customers.json` |

Each file is loaded by one repository class in `app/data/repositories.py`. The registry gives every agent a view
that exposes only its declared sources, and tools reach data through `ctx.data` (for example
`ctx.data.transactions.for_customer(ctx.customer_id, since=ctx.data.since(30))`). A tool that touches a source its
agent didn't declare gets an error, not the data. `GET /v1/agents` lists each agent's sources and files.

Policies are markdown with `id` and `title` front matter; A13 searches them paragraph by paragraph and cites the id.
"Today" is pinned by `TODAY` (default 2026-11-27) and the folder can be moved with `DATA_DIR`.

To swap in a real system, replace one repository class and keep its methods.

## Adding an agent

Add a module in `app/agents/` with tools (`@tool(...)`) and a `build()` returning an `Agent` with its `sources`.
If it needs a new data file, add it under `data/` and register its repository in `SOURCES`. Then append it to
`AGENT_BUILDERS` in `app/agents/registry.py`. The router picks it up from the catalog automatically.

## Production swap list

| Demo | Production |
| --- | --- |
| `data/` files via `app/data/repositories.py` | systems of record (one repository class per source) |
| keyword policy search | vector store |
| in-memory sessions | Redis |
| JSONL audit | log pipeline / SIEM |
| `customer_id` in body | auth claims |
| in-process agents | one MCP server per agent domain |
