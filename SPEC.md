# Agent Orchestration Platform: Build Spec for Claude Code

Nov 27, 2026 · @Anirudh Yadav

## Goal and how to use this spec

Build a runnable multi-agent RAG platform that an interviewer can clone from GitHub and run locally. A customer types one question, the platform chooses the right domain agents automatically, each agent works only with its own tools, and the customer gets one answer. The deliverable has three parts:

1. **The platform:** API, agents, orchestration and tests. It runs offline on a free mock LLM, or on real Claude when an API key is present.
2. **Real Claude integration** with a hard $5 spend cap inside the app, on top of the Console workspace limit.
3. **A before/after comparison page.** Left: the user picks agents (today's system). Right: the platform picks them. Both sides run the same pipeline, so the same agents give the same tools and the same answer.

The live demo must handle three query types: ambiguous, a single agent using 2 tools, and several agents using 1 tool each.

How to use it with Claude Code:

1. Export this doc as Markdown and save it as `SPEC.md` at the root of the repo. If you have the reference implementation (`agent_platform.zip`, which already passes 31 tests and implements everything here except the plan cache), unzip it first and tell Claude Code to extend it rather than start over.
2. Prompt Claude Code: *"Read SPEC.md and build the project exactly as specified. Follow the build order step by step, run the tests after each step, and do not finish until every acceptance test passes and the fresh-clone check in the definition of done succeeds."*
3. The project must run fully offline on a deterministic mock LLM. `LLM_PROVIDER=auto` (the default) switches to Claude whenever `ANTHROPIC_API_KEY` is set, with no code changes.

Design principles. These are non-negotiable, and Claude Code must not trade them away for convenience:

- **LLMs understand and write; code decides.** Routing, planning, execution order, retries, caching and spend limits are deterministic code. LLMs only read the question, pick tools inside an agent, and write the answer.
- **Ambiguity gets a clarifying question, never a guess.** If the router flags ambiguity or confidence is below the threshold, the customer is asked what they meant and no agent runs.
- **Agents are isolated.** An agent can only call its own tools. `customer_id` is injected by the platform and never chosen by an LLM.
- **One pipeline, two entry points.** Manual agent selection (before) and automatic routing (after) share everything downstream of the agent choice. That's what makes them directly comparable.
- **The cache stores plans, never answers.** Tools always run live, so data is fresh and nothing leaks between customers.
- **Spend is bounded.** Every paid LLM call reserves its worst-case cost first and is refused if it could exceed the budget.
- **Everything is traceable.** Every stage writes an audit event under one `trace_id`, with card numbers, SSNs and emails redacted.

## Architecture

Every question passes intent pre-processing, then the plan cache decides between two routes. A hit runs the cached tools directly and collates; a miss, or a "more details" request, goes through the score gate and the full orchestration graph.

```
question
  │
  ▼
Intent pre-processing ──► Plan cache
                            │
             hit ◄──────────┴──────────► miss, or "more details"
              │                                   │
              ▼                                   ▼
   Fast path: run cached tools live         Intent score
              │                     ┌─────────┼──────────────┐
              ▼                     ▼         ▼              ▼
      Collate (fast path)        clarify  out of scope   orchestrate
              │                     │         │              │
              │                     │         │              ▼
              │                     │         │     Orchestrator ◄─────┐
              │                     │         │              │         │ loop back while tasks
              │                     │         │              ▼         │ wait on dependencies
              │                     │         │     Executor → Agents  │ or can be retried
              │                     │         │              │         │
              │                     │         │              ▼         │
              │                     │         │     Response collator ─┘
              ▼                     ▼         ▼              ▼
                            final answer + audit log
```

The collator loops back to the orchestrator while tasks are waiting on dependencies or can be retried. Only the clarify route answers without running any agent.

Manual selection (the "before" mode) enters the same pipeline with two differences, and nothing else changes:

- **Router scope:** intent pre-processing only sees the agents the user ticked, and is told not to flag ambiguity. It still splits the question between those agents.
- **No score gate:** a forced-acceptance step replaces it. Every ticked agent is accepted, ordered the way the router ordered them, with dependencies kept only between ticked agents. A ticked agent that the router found no part of the question for receives the whole question.

The result is that ticking the agents the question really needs gives exactly the same tasks, tools and answer as automatic routing.

| Component | Responsibility | LLM calls per request |
| --- | --- | --- |
| Client + session | Receives the question or a "more details" request; keeps session history and checks that a session belongs to the customer | 0 |
| Intent pre-processing | Normalises and redacts input; one LLM call returns one intent per agent (sub-query, confidence, dependencies) plus extracted slots | 1 |
| Plan cache | Matches on agent set, slot names and question similarity; returns a plan template or a miss | 0 (1 embedding) |
| Fast path | Fills slots, runs the cached tools live for this customer, stage by stage; falls back to the full graph on any tool failure | 0 |
| Collate (fast path) | Writes the answer from the tool outputs | 1 |
| Intent score | Rule-based gate: validates agents, scores intents, returns orchestrate, clarify or out of scope | 0 |
| Orchestrator | Builds the task graph from accepted intents; on loop-back, schedules retries and skips tasks blocked by failed dependencies | 0 |
| Executor | Runs every ready task in parallel with a timeout per agent; passes upstream outputs to dependent tasks | 0 |
| Agent (A11–A14) | Tool-use loop over its own tools only | 2–4 each |
| Response collator | Loops back or composes the final answer; saves the plan template to the cache after a fully successful run | 1 when 2+ agents or detail mode, else 0 |
| Audit log | Records every stage under one `trace_id`, with redaction | 0 |

## Agents and their tool sets

There are four agents in this build, each owning 1–2 tools in its domain. A15 is an open slot that proves a new agent can be added without changing the platform.

| Agent | Domain | Tool | Inputs (LLM-supplied) | Returns |
| --- | --- | --- | --- | --- |
| A11 | Credit card recommendation | `get_customer_profile` | none | credit band, income band, existing cards |
| A11 | Credit card recommendation | `recommend_cards` | `query`, `top_n` (default 2) | eligible cards ranked by net annual value from the last 30 days' spend |
| A12 | Transaction queries | `search_transactions` | `query`, `merchant`?, `category`?, `status`?, `days` (default 30) | matching transactions, count, total |
| A12 | Transaction queries | `get_dispute_status` | `transaction_id`? | case id, stage, provisional credit, expected days |
| A13 | Policy knowledge base (RAG) | `search_policies` | `query`, `top_k` (default 2) | policy chunks with ids for citation |
| A14 | Personal finance (PFM) | `spending_summary` | `category`?, `days` (default 30) | total and breakdown by category |
| A14 | Personal finance (PFM) | `budget_status` | none | spend vs budget per category, over-budget list |

Tool contract, which every tool must follow:

- **MCP-shaped spec:** `name`, `description`, `input_schema` (JSON Schema). The same tools must be exposable through an MCP server per agent without changes.
- **Identity injection:** the tool function signature is `fn(ctx: ToolContext, **args)`. `ctx` carries `customer_id`, `session_id`, `trace_id` and `upstream` (outputs of agents this one depends on). Tools reject unknown or missing arguments and invalid enum values.
- **Output:** a JSON dict that always includes a one-sentence `summary` field. The mock LLM and the cache fast path rely on it.
- **Errors:** tool errors are returned to the agent as `{"error": ...}` with `is_error`, never raised to the platform.

Agent contract:

- An agent has `id`, `name`, `description`, `keywords`, `system_prompt` and `tools`. Its card (everything except the prompt) is what the intent layer sees.
- It runs a tool-use loop: LLM picks tools, the platform runs them, results go back to the LLM, and the LLM writes the agent's answer. Cap the loop at `AGENT_MAX_TOOL_STEPS` (default 4).
- An agent registers by adding one module with `build()` to the registry. The intent layer picks it up from the catalog automatically.
- A13 is the RAG agent. Policy documents are chunked (about 300 tokens, 50 overlap), embedded and stored in a vector index. `search_policies` embeds the query, returns the top-k chunks with policy ids, and the agent must cite them as `[POL-…]`.

Details the scenarios depend on:

- **Routing keywords:** A11 — card, credit card, recommend, rewards, cashback, which card. A12 — card, transaction, charge, payment, purchase, pending, dispute, refund. A13 — card, policy, fee, late, terms, foreign, limit increase. A14 — spend, spending, budget, save, savings, expenses. "card" is deliberately in A11, A12 and A13, so "What about my card?" is ambiguous.
- **Category filter:** `spending_summary.category` is an enum (dining, groceries, travel, shopping). With a category it returns that category's total; without one it returns the total and the biggest category.
- **Money formatting:** every tool `summary` formats amounts as `$1,234.56`.
- **The `route_intents` tool:** the router answers through it. Its schema is `{intents: [{agent_id, sub_query, confidence 0..1, depends_on[], rationale}], ambiguous: bool, clarifying_question: str}`, and both `intents` and `ambiguous` are required. When `ambiguous` is true, the score gate returns `clarify` with the router's question, and no agent runs.

## Sample dataset

The dataset is fixed so the acceptance tests have exact expected answers. "Today" is pinned to 2026-11-27 through `settings.today`, never `date.today()`. Store the data as files under `data/`, loaded through repository classes, so swapping them for real systems later is one class per source.

`data/customers.json`, where C2002 exists only to prove one customer can never see another's data:

```json
[
  {"customer_id": "C1001", "name": "Demo Customer", "credit_score_band": "good", "annual_income_band": "100k-150k", "existing_cards": ["CARD_CASHBACK_BASIC"], "monthly_budget": {"dining": 300, "groceries": 600, "travel": 400, "shopping": 350}},
  {"customer_id": "C2002", "name": "Second Customer", "credit_score_band": "fair", "annual_income_band": "50k-100k", "existing_cards": [], "monthly_budget": {"dining": 150, "groceries": 400, "travel": 100, "shopping": 200}}
]
```

`data/transactions.csv`:

```csv
id,customer_id,date,merchant,category,amount,status
T9001,C1001,2026-11-26,Blue Bottle Cafe,dining,18.40,posted
T9002,C1001,2026-11-25,FreshMart,groceries,142.75,posted
T9003,C1001,2026-11-24,SkyHigh Airlines,travel,612.00,posted
T9004,C1001,2026-11-22,Online Store 24,shopping,89.99,disputed
T9005,C1001,2026-11-21,Trattoria Roma,dining,96.20,posted
T9006,C1001,2026-11-18,FreshMart,groceries,118.30,posted
T9007,C1001,2026-11-15,CityRide,travel,32.50,posted
T9008,C1001,2026-11-13,Sushi Bar,dining,74.10,pending
T9009,C1001,2026-11-07,HomeGoods,shopping,210.00,posted
T9010,C1001,2026-11-02,FreshMart,groceries,131.60,posted
T9101,C2002,2026-11-20,Book Nook,shopping,45.00,posted
```

These totals for C1001 over the last 30 days are used by the tests: dining 188.70, groceries 392.65, travel 644.50 (over its 400 budget), shopping 299.99, all categories 1,525.84.

`data/disputes.json`:

```json
[{"transaction_id": "T9004", "case_id": "D-55120", "opened": "2026-11-23", "stage": "under_review", "provisional_credit": true, "expected_resolution_days": 10}]
```

`data/card_products.json`, where reward rates are % back by category and `default` applies to everything else:

```json
[
  {"id": "CARD_TRAVEL_PLUS", "name": "Travel Plus Card", "annual_fee": 95, "min_score_band": "good", "rewards": {"travel": 3.0, "dining": 2.0, "default": 1.0}},
  {"id": "CARD_DINING_REWARDS", "name": "Dining Rewards Card", "annual_fee": 0, "min_score_band": "fair", "rewards": {"dining": 3.0, "groceries": 2.0, "default": 1.0}},
  {"id": "CARD_CASHBACK_FLAT", "name": "Flat 2% Cash Back Card", "annual_fee": 0, "min_score_band": "good", "rewards": {"default": 2.0}}
]
```

`data/policies/*.md` are the RAG corpus, one markdown file per policy:

| File | Policy id | Content |
| --- | --- | --- |
| `dispute.md` | POL-DISPUTE-01 | Disputes allowed within 60 days of the statement date; provisional credit usually within 10 business days while the claim is reviewed. |
| `foreign-fees.md` | POL-FEES-02 | 3% foreign transaction fee on purchases in a foreign currency, except on cards that list no foreign transaction fees. |
| `late-payment.md` | POL-LATE-03 | A $35 late fee applies if the minimum payment is not received by the due date; one courtesy waiver per 12 months on request. |
| `credit-limit.md` | POL-LIMIT-04 | Limit increase requests reviewed after 6 months of account history; may involve a soft credit inquiry. |

Claude Code should write each policy as 2–3 short paragraphs around the content above, so chunking and retrieval are exercised on real text.

## Demo scenarios and acceptance tests

Each row below is a pytest acceptance test run as customer C1001 on the mock LLM, and also a line in `demo.py`. Rows 1–3 are the three cases the interviewer asked for. Rows 4–8 prove dependencies, caching and isolation. Every response must include `status`, `path` (`intent`, `cache` or `full`), the agents and tools used, and `llm_calls`.

| # | Case | Query | Expected route | Agents → tools | Answer must contain | LLM calls |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Ambiguous | "What about my card?" | `clarify`, path `intent`, no agent runs | none | a question offering card recommendation, card transactions and card policies | 1 |
| 1b | Out of scope | "Tell me a joke" | `out_of_scope`, path `intent` | none | the list of things the assistant can help with | 1 |
| 2 | Single agent, 2 tools | "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?" | `ok`, path `full` | A12 → `search_transactions` then `get_dispute_status` (T9004 taken from the first tool's result) | 89.99, D-55120, under review, provisional credit | 4 (intent 1 + agent 3) |
| 3 | Multi-agent, 1 tool each | "What is the late payment fee, and how much did I spend on dining this month?" | `ok`, path `full`, both agents in one parallel wave | A13 → `search_policies`; A14 → `spending_summary(category=dining)` | $35, \[POL-LATE-03\], 188.70 | 6 (intent 1 + 2 + 2 + collate 1) |
| 4 | Dependency | "How much did I spend this month and which credit card suits my spending?" | `ok`, path `full`, `orchestrator_loops` = 2 | A14 → `spending_summary` first; then A11 → `recommend_cards` using A14's output | 1,525.84, Flat 2% Cash Back Card | 6 |
| 5 | Cache hit | Run row 3 first, then: "How much have I spent on dining this month and what's the late fee?" | `ok`, path `cache` | same tools, run live from the cached plan | $35, \[POL-LATE-03\], 188.70 | 2 (intent + collate) |
| 6 | Cache with a new slot value | After row 3: "How much did I spend on travel this month, and what's the late fee?" | `ok`, path `cache` | `spending_summary(category=travel)` from the same plan template | 644.50, \[POL-LATE-03\] | 2 |
| 7 | More details | After row 5, in the same session: "more details please" | `ok`, path `full`, detail mode | re-runs row 5's question through the full graph | the row 5 facts plus a fuller, itemised answer | 6 |
| 8 | Isolation | Row 2's query as customer C2002 | `ok` or `partial`, path `full` | A12 tools scoped to C2002 | no T9004, no D-55120, nothing from C1001 | 4 |

Required behaviour of the mock LLM, so the table holds without an API key:

- **Intent:** split the message into clauses and score each clause against agent `keywords`. Confidence is `min(0.95, 0.5 + 0.2 × hits)`. When the top score in a clause ties across two or more agents, emit every tied agent at confidence 0.45, so the score gate returns `clarify` with those agents as the options. The word "card" must appear in the keywords of A11, A12 and A13, so row 1 ties.
- **Entities:** extract slots with simple rules: `category` (dining, groceries, travel, shopping), `merchant` (matched against known merchant names), `amount`, `transaction_id` (`T\d+`) and `days` ("this month" means 30).
- **Agent loop:** pick the tool whose name and description overlap the sub-query most. After a result, call another unused tool of the same agent if its keywords match the sub-query, passing ids found in earlier results (row 2's T9004). Then answer from the `summary` fields.
- **Collate:** combine each agent's output into one reply. In detail mode, include every figure from the tool data.

Other tests Claude Code must write: an agent timeout and a failing agent (retried, then `partial`), a dependent task skipped when its upstream fails, tool argument validation, audit redaction of a card number, cache expiry by TTL, cache invalidation when a tool schema changes, and the HTTP API end to end.

Before/after consistency tests. Each runs the same question twice, once with `agent_ids` (manual) and once without (auto), as C1001 on the mock:

| # | Agents ticked | Query | Expected |
| --- | --- | --- | --- |
| 9 | A12 | row 2's query | identical tools (`search_transactions`, `get_dispute_status`) and identical answer; manual `path` = `manual`, auto `path` = `full` |
| 10 | A14, A13 (tick order must not matter) | row 3's query | identical tools and answer; 6 LLM calls on each side |
| 11 | A11, A14 | row 4's query | identical tools and answer; A14 still runs before A11 |
| 12 | A14 only | row 3's query | manual answers only the dining part (188.70) with no \[POL-LATE-03\]; auto answers both parts |
| 13 | A11 only | "What about my card?" | manual answers from A11; auto returns `clarify` |
| 14 | none | any | `error`, "Select at least one agent before asking.", 0 LLM calls |

Claude-path tests use a fake `AsyncAnthropic` client that returns real SDK `Message` objects. Each response carries a signed thinking block and a `usage` block, and the fake enforces the API's 400 rules (see the Claude integration section). These tests run rows 1, 2 and 3 through the real provider code and must prove:

- the router uses Haiku with forced `route_intents` and thinking disabled
- agents and the collator use Sonnet with `between_tools` and no forced tool
- thinking blocks are replayed unchanged
- cost is recorded and persisted to the ledger
- the budget stops calls before they overspend, and no API request goes out after the stop
- the per-request call cap holds
- a missing key gives a clear error

## Plan cache and "more details"

**Status:** designed but not yet in the reference implementation. Build it last (step 9), only once every other test passes. Applying it to manual mode is optional, but a manual request must never read a plan built for a different agent set.

A cache hit cuts a typical two-agent question from 6 LLM calls to 2. It does this by reusing the plan (which agent runs which tool, with which argument slots, in which stage), never the answer. The cache is checked right after intent pre-processing.

What a cached entry holds:

- **Plan template:** ordered steps of `{agent_id, tool, arguments, stage}`. Arguments are slots such as `{category}`, `{merchant}`, `{transaction_id}`, `{days}` or `{sub_query}`, never literal values from the original question. Steps in the same stage run in parallel; later stages receive upstream outputs.
- **Match key:** the sorted set of agent ids, plus the set of slot names the plan needs, plus the embedding of the normalised and redacted question.
- **Metadata:** created time, hit count, negative-feedback count, and the tool catalog version (a hash of every agent's tool schemas).

Lookup rules. A hit needs all of these, otherwise it's a miss:

1. Same agent set from intent pre-processing.
2. Every slot the plan needs is present in the current question's extracted entities. A missing slot is a miss, never a guess.
3. Cosine similarity of at least `CACHE_SIMILARITY` (default 0.80).
4. The entry is within `CACHE_TTL_S`, was built against the current catalog version, and has fewer than `CACHE_MAX_NEGATIVE` (default 2) "more details" requests against it.

On a hit, the fast path fills the slots from the current entities and runs the tools for the current customer, stage by stage. It skips intent scoring, the orchestrator, the executor and every agent's LLM loop, then makes one collate LLM call. If any tool fails, it falls back to the full graph within the same request.

The plan is written to the cache only after a full run that ends with status `ok`. It's built from the tools that actually ran successfully, and literal values are converted back to slot names using the extracted entities.

More details:

- The trigger is either the API flag `more_details: true` or a short follow-up (8 words or fewer) matching phrases like "more details", "elaborate", "tell me more", "not helpful" or "go deeper".
- Behaviour: re-run the previous question in the session through the full graph with `detail=true`, bypassing the cache. The collator always makes its LLM call in detail mode and is told to give a fuller, itemised answer.
- Feedback: if the previous answer came from the cache, add 1 to that entry's negative count, then replace the entry with the plan from this full run. A longer message containing a detail phrase is treated as a new question that bypasses the cache.

The offline embedder is a dependency-free hashing embedder (word and character-trigram features in a 1,024-dimension vector, L2-normalised). It sits behind an `Embedder` interface so a real sentence-embedding model can replace it. The cache store sits behind a `PlanStore` interface, with an in-memory implementation now and Redis with a vector index for production.

## Claude API integration and spend protection

The app uses a small, fast model to route and a stronger model to work the tools and write answers. Spend is capped twice: by a $5 Console workspace limit, and by a guard inside the app that refuses any call that could cross the budget. Prices and model rules below are from the Claude API docs as of 2026-11-27. Prices sit in one table in code, so they can be updated when they change.

| Step | Env var | Default model | Price per 1M tokens (in / out) | Request settings |
| --- | --- | --- | --- | --- |
| Intent routing | `INTENT_MODEL` | `claude-haiku-5-5` | $0.10 / $0.50 (prompts up to 100k tokens) | `thinking: {type: disabled}`, forced `tool_choice: {type: tool, name: route_intents}` |
| Agents | `AGENT_MODEL` | `claude-sonnet-5-5` | $2 / $10 | `thinking: {type: between_tools}`, no forced tool choice |
| Collator | `COLLATE_MODEL` | `claude-sonnet-5-5` | $2 / $10 | `thinking: {type: between_tools}` |

Model rules the provider must handle:

- **Sonnet 5.5 rejects two settings:** `thinking: {type: disabled}` and forced tool choice both return a 400 error. Send `between_tools` to turn off up-front thinking, and downgrade a forced choice to `{type: any}`.
- **Haiku 5.5 accepts both**, so the router can force `route_intents` with thinking off.
- **Thinking blocks must be returned unchanged.** In tool loops, every assistant turn goes back to the API exactly as received, including signed thinking blocks. Keep each response's raw content blocks; never rebuild them from text and tool calls.
- **Fallback on a rejected setting:** if a request is rejected over `thinking` or `tool_choice`, retry it once without those overrides.
- **Thinking settings by exact model id:** look them up per exact id. An unknown model gets the API default.

Spend protection:

- **`MeteredLLM` wraps every provider.** Per request (a context variable shared by the agents running in parallel), it counts calls and refuses past `MAX_LLM_CALLS_PER_REQUEST` (default 12).
- **`BudgetGuard` checks before every paid call.** It reserves the worst-case cost (input estimated at 3 characters per token, plus `max_tokens` of output, default 2,048) and raises `BudgetExceeded` if spent + reserved + this call could pass `BUDGET_USD` (default 5.0). After the call it settles the reservation to the actual cost from `usage.input_tokens` and `usage.output_tokens`, and on an error it releases it.
- **Ledger:** actual spend is written to `.usage/ledger.json` (spent, calls, tokens, by model) and reloaded at start-up. Because it's per machine, the Console workspace limit is the authoritative cap.
- **Unknown models** are priced at $10 / $50, so the guard errs on the safe side.
- **Every response** reports `llm_calls`, `llm_calls_by_step`, `cost_usd` and `budget_remaining_usd`. `GET /v1/usage` returns the ledger.

Keys and errors:

- `ANTHROPIC_API_KEY` is read from the environment or a git-ignored `.env`, and is never logged.
- `LLM_PROVIDER=anthropic` without a key fails at start-up, naming the variable to set.
- API failures map to plain messages:
  - `AuthenticationError`: the key was rejected, check `.env`.
  - `PermissionDeniedError`: the key isn't allowed to use this model or workspace.
  - `RateLimitError`: rate or spend limit reached.
  - `APIConnectionError`: can't reach the API.
- A budget stop returns status `budget_exceeded`, and no further API request is sent.

Setup for a shared demo key: create a dedicated Console workspace, set its monthly spend limit to $5, create the key inside it, send the key privately, and revoke it afterwards. A full two-agent question costs about $0.02–0.06, and an ambiguous one about $0.002.

## Before/after comparison page

One page, served by the app at `GET /`, sends the same question to both sides and proves the design: tick the agents a question really needs, and both sides show the same agents, the same tools and the same answer. Tick the wrong ones, or ask something ambiguous, and the page shows exactly where they differ. It's a single self-contained `app/static/index.html` with vanilla JS and no build step.

Layout, top to bottom:

1. **Header:** title "Ask once, compare", the model in use ("Claude" or "Offline mock (free)" from `/health`), budget left (Claude only), and a customer picker (C1001, C2002). Changing customer starts over.
2. **Question bar:** one textarea and an "Ask both" button (Enter submits, Shift+Enter adds a new line). Below it are example chips: Ambiguous, One agent with two tools, Two agents with one tool each, One agent needs another. Each chip fills the question box with its row's query. A "Start over" link clears both threads and sessions.
3. **Comparison strip:** hidden until both sides answer, then a one-line headline plus three facts.
   - Agents: same or different, with "you picked X; the platform picked Y".
   - Tools: same or different, listing them.
   - Cost and calls for each side.
   - Headline rules:
     - Right side returned `clarify`: "the platform asked what you meant instead of guessing".
     - Same agents, tools and answer text: "Both sides match".
     - Same agents and tools but different text: wording differs because each side is a separate model response. This is expected on live Claude.
     - Otherwise: "your selection didn't match what the question needed".
   - Green left border when agents and tools match, red otherwise.
4. **Two columns** (stacked below 900 px):
   - **Before, "You choose the agents":** a grid of A11–A14 checkboxes, each showing the agent name and its tools. A hint line shows how many are selected. If you ask with none ticked, nothing is sent and the hint explains that this is the step the new way removes.
   - **After, "The platform chooses":** no checkboxes and no agent list. It's a plain chat.
   - **Each answer on both sides shows:** the question, a status pill (Answered, Asked you to clarify, Partly answered, Not answered, Spending limit reached), the answer text, a facts row (agents picked, tools run, LLM calls, cost, time), and an audit trail link to `/v1/audit/{trace_id}`.

Behaviour: "Ask both" calls `POST /v1/chat/manual` (with the ticked `agent_ids`) and `POST /v1/chat` in parallel, keeps a separate `session_id` per side, then renders the strip. Escape all server text before inserting it. Visual direction: cool paper background, white panels, ochre for "before", deep teal for "after", Public Sans with system fallback, tabular numbers, visible keyboard focus, and reduced motion respected.

## Scalability

The design scales on four separate axes: traffic, number of agents, number of tools per agent, and LLM cost. Each is handled by a specific mechanism, not by a single bigger model. Claude Code must build every infrastructure dependency behind an interface, with an in-memory version for the demo, so moving to production means swapping implementations, not rewriting logic.

| Concern | Design choice | Demo implementation | Production implementation |
| --- | --- | --- | --- |
| Request volume | Stateless API pods; all state (sessions, cache, audit) outside the process | in-memory stores behind interfaces | Kubernetes HPA; Redis for sessions and cache |
| Slow or hot agents | Agents are independent units behind MCP-shaped tool contracts; per-agent timeout, retry and skip | in-process agents, `asyncio` parallel waves | one MCP server per agent domain, scaled and deployed independently; circuit breaker per agent |
| Adding agents | Routing is driven by the agent catalog, so a new agent is one registration with no platform change | registry list | catalog service; when it grows past about 20 agents, retrieve the top-k agent cards by embedding before the intent LLM call |
| Many tools per agent | Tool choice happens inside the agent, out of the router's context | 1–2 tools each | tool retrieval (top-k tool specs per sub-query) when an agent has more than about 15 tools |
| LLM cost and latency | Plan cache (6 calls down to 2); single-agent answers skip collate; model tiering | mock LLM with a call counter | small model for intent, larger model for agents and collate; streamed responses |
| RAG corpus growth | Retrieval behind a `VectorIndex` interface | hashing embedder, in-memory index | managed vector store such as pgvector; re-index on policy change |
| Traceability at scale | One `trace_id` across every stage | JSONL audit file | OpenTelemetry traces; append-only audit store |

Versus an LLM supervisor that delegates to agents: a supervisor is simpler to build and more flexible on unusual questions, but it adds an LLM decision at every hop. That makes cost, latency and routing behaviour harder to predict, test and audit. This design keeps the LLM for understanding and writing and keeps control flow in code. The ambiguity gate and the plan cache are only possible because routing is explicit.

To show scalability rather than just claim it, Claude Code must add `bench.py`. It fires N concurrent requests (default 200) with a mix of rows 2–5 and prints p50/p95 latency, requests per second, cache hit rate and average LLM calls per request. With the mock LLM it adds a configurable simulated latency per call (default 400 ms), so the effect of the cache and parallel waves is visible.

## Tech stack, layout, API and build order

Use Python 3.11+ with FastAPI, Pydantic v2, asyncio and pytest. Import the `anthropic` SDK lazily, only in Claude mode. Do not add LangChain or LangGraph: the orchestration graph is a small in-repo state graph (nodes, edges, conditional edges, a step limit), so every control decision stays visible and testable.

Pinned `requirements.txt` (verified working together on Python 3.11 and 3.13):

```
anthropic==2.14.0
fastapi==0.165.0
uvicorn==0.54.0
pydantic==2.13.5
python-dotenv==1.2.3
httpx==0.29.0
pytest==9.1.1
```

Project layout:

```
agent_platform/
  README.md  requirements.txt  Makefile  demo.py  bench.py  .env.example  .gitignore
  .github/workflows/ci.yml          tests on Python 3.11 / 3.12 / 3.13, mock mode
  app/
    main.py          FastAPI routes + serves the comparison page
    platform.py      AgentPlatform.handle(message, customer, session, agent_ids=None): one pipeline, two entry points
    config.py        settings from env / .env; LLM_PROVIDER=auto
    models.py        Intent, ScoredIntent, Task, AgentResult, FinalResponse(path: intent|full|manual|cache|none)
    session.py       SessionStore (in-memory; customer ownership check)
    static/index.html  before/after comparison page
    llm/             base.py (LLMResponse keeps raw_content), anthropic_llm.py, mock.py,
                     budget.py (BudgetGuard, prices), metered.py (MeteredLLM, per-request meter)
    intent/          preprocessor.py (route_intents tool, allowed agents), evaluator.py (score gate + forced)
    orchestrator/    graph.py, orchestrator.py, executor.py, collator.py, state.py
    agents/          base.py, registry.py, cc_recommend.py (A11), transactions.py (A12), kb_policies.py (A13), pfm.py (A14)
    tools/           base.py (Tool, ToolContext, @tool)
    data/            fixtures or repositories over data/ files
    audit/           logger.py with redaction
    cache/           (step 9) embedder.py, plan_cache.py, fast_path.py
  tests/             test_scenarios.py, test_platform.py, test_claude_integration.py, fake_anthropic.py
```

API:

| Method | Path | Body or result |
| --- | --- | --- |
| GET | `/` | the before/after comparison page |
| POST | `/v1/chat` | after: `{customer_id, message, session_id?}` → `FinalResponse {session_id, trace_id, status, path, response, context{intents, agents, tools_used, llm_calls, llm_calls_by_step, cost_usd, budget_remaining_usd?, latency_ms, orchestrator_loops}}` |
| POST | `/v1/chat/manual` | before: the same body plus `agent_ids` → the same `FinalResponse`, `path` = `manual` |
| GET | `/v1/usage` | spend ledger against `BUDGET_USD` (Claude mode) |
| GET | `/v1/audit/{trace_id}` | every event for one request |
| GET | `/v1/sessions/{session_id}` | history |
| GET | `/v1/agents` | the agent catalog with tool names |
| GET | `/health` | provider, agent ids; in Claude mode also models and budget left |

In the demo, `customer_id` comes in the request body. The README must say that in production it comes from the auth token.

Environment variables, with `.env.example` documenting each one:

- **Provider:** `ANTHROPIC_API_KEY`, `LLM_PROVIDER` (auto)
- **Models:** `INTENT_MODEL` (claude-haiku-5-5), `AGENT_MODEL` and `COLLATE_MODEL` (claude-sonnet-5-5)
- **Request limits:** `LLM_MAX_TOKENS` (2048), `LLM_TIMEOUT_S` (60)
- **Spend:** `BUDGET_USD` (5.0), `MAX_LLM_CALLS_PER_REQUEST` (12), `USAGE_LEDGER_PATH` (.usage/ledger.json)
- **Routing:** `INTENT_THRESHOLD` (0.55)
- **Orchestration:** `MAX_ORCHESTRATOR_LOOPS` (3), `MAX_TASK_ATTEMPTS` (2), `AGENT_TIMEOUT_S` (90), `AGENT_MAX_TOOL_STEPS` (4)
- **Other:** `AUDIT_PATH`, `TODAY` (2026-11-27)
- **Step 9 only:** the `CACHE_*` variables and `MOCK_LLM_LATENCY_MS`

`.gitignore` must cover `.env`, `.usage/`, `audit.jsonl`, caches and virtual environments.

Build order. Run the tests at the end of each step:

1. Models, config (with `.env` loading), the sample data. Test that the totals in the dataset section load exactly.
2. Tool contract and the four agents with their tools. Unit-test every tool directly, including customer isolation.
3. RAG: chunker, embedder, vector index, ingest of `policies/`. Test that "late payment fee" retrieves POL-LATE-03 first.
4. LLM layer:
   - the interface with raw content blocks
   - the mock LLM (keyword router through `route_intents`, tie means ambiguous, tool chaining, enum argument filling)
   - `MeteredLLM` and `BudgetGuard`
   - the Anthropic provider with the model rules
   - the fake SDK client and the Claude-path tests
5. Intent pre-processor and score gate, plus forced acceptance for manual mode. Test rows 1 and 1b.
6. Orchestration graph: orchestrator, executor (parallel waves), collator (loop back). Test rows 2, 3, 4 and 8, then the before/after consistency rows 9–14.
7. Audit with redaction, FastAPI routes including `/v1/chat/manual` and `/v1/usage`, and clear API error messages.
8. Comparison page, `demo.py`, README, Makefile, CI workflow, then the fresh-clone check.
9. Plan cache, fast path, more details, and `bench.py`. Test rows 5, 6 and 7, TTL and catalog-version invalidation.

## Definition of done and demo script

The build is done when a fresh `git clone` on a clean machine runs the demo, the page and the whole test suite offline, every box below is ticked, and one live Claude run has been checked by a person.

- [ ] Fresh-clone check: clone into a new folder, create a new virtual environment, `pip install -r requirements.txt`, then `make test` and `python demo.py`, with no `ANTHROPIC_API_KEY` set. Repeat on Python 3.11.
- [ ] Every scenario row (1–8) and consistency row (9–14) is an acceptance test and passes, including the exact LLM call counts
- [ ] Claude-path tests pass using the fake SDK client (model per step, request settings, thinking replay, ledger, budget stop, call cap, missing key)
- [ ] Each agent can call only its own tools; no tool accepts `customer_id`; the C2002 isolation tests pass
- [ ] Audit events never contain a raw 16-digit card number
- [ ] `make serve` and `http://localhost:8000`:
  - With A13 and A14 ticked, the two-agent question shows a green "Both sides match" strip.
  - With only A14 ticked, the strip is red and names the difference.
  - "What about my card?" shows the right side asking to clarify.
  - There are no console errors.
- [ ] CI workflow passes on GitHub for Python 3.11, 3.12 and 3.13
- [ ] Live check (manual, by a person with the key): with `.env` set, `python demo.py` gives answers on Claude for rows 1–4. The total cost printed is under $0.30, and `GET /v1/usage` matches it.
- [ ] With an invalid key, the answer says the key was rejected and $0 is recorded
- [ ] README covers:
  - quickstart without a key
  - running with Claude and the two-layer spend protection
  - the comparison page
  - the scenarios
  - the architecture
  - LLM calls per request
  - how to add agent A15
  - the production swap list
- [ ] Step 9 (plan cache and `bench.py`) is done, or the README marks it as next

What `demo.py` prints per query:

```
[3] Multi-agent, 1 tool each
USER    What is the late payment fee, and how much did I spend on dining this month?
ROUTE   status=ok  path=full  agents=A13,A14  llm_calls=6  cost=$0.0312
TOOLS   A13.search_policies  A14.spending_summary
ANSWER  <the combined response>
TRACE   trace_…  (15 audit events)
```

Running the interview demo:

1. `make serve`, then open `http://localhost:8000`.
2. Run the four example chips on both sides, ticking the right agents on the left, so the strip shows the two sides match.
3. Then tick a wrong agent, and ask the ambiguous question, to show the difference.
4. Open one audit trail link to show every decision is traceable.
5. Finish with `GET /v1/usage` to show spend is tracked against the cap.
