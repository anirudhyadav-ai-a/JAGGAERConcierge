# How to use it

There are three ways in: the comparison web page, the HTTP API, and `demo.py`. All three run the same pipeline.
Start the server with `make serve` (see [running.md](running.md)).

## The comparison page

There are two versions of the page and they behave the same:

- **React app:** http://localhost:5173 with `make dev`, or http://localhost:8000/app/ after `make frontend-build`
- **Original single-file page:** http://localhost:8000

One question goes to two sides at once:

- **Before: "You choose the agents".** Tick the agents (A11–A14) you think the question needs. Each box shows
  the agent's name and tools.
- **After: "The platform chooses".** A plain chat. The platform picks the agents itself.

Steps:

1. Pick a customer at the top (C1001 is the main demo customer; C2002 proves one customer can't see another's
   data). Changing customer starts over.
2. Type a question, or click an example chip: *Ambiguous*, *One agent with two tools*, *Two agents with one tool
   each*, *One agent needs another*.
3. On the left, tick the agents. If you ask with none ticked, nothing is sent; that is the step the platform removes.
4. Press **Ask both** (Enter sends, Shift+Enter adds a line).

A strip under the question compares the two answers:

| Strip says | Meaning |
| --- | --- |
| Both sides match (green) | same agents, same tools, same answer |
| Same agents and tools, wording differs | expected on live Claude: each side is a separate model response |
| The platform asked what you meant instead of guessing | the question was ambiguous, so no agent ran on the right |
| Your selection didn't match what the question needed (red) | you ticked different agents than the question needs |

Each answer shows a status (Answered, Asked you to clarify, Partly answered, Not answered, Spending limit reached),
the agents and tools used, LLM calls, cost, time, and a link to its audit trail. **Start over** clears both sides.

The header shows which model is in use ("Claude" or "Offline mock (free)") and, on Claude, the budget left.

## Questions to try

| Try | What happens |
| --- | --- |
| What about my card? | ambiguous: you're asked whether you mean recommendations, transactions or policies |
| Tell me a joke | out of scope: you get the list of things it can help with |
| What's happening with my $89.99 Online Store 24 charge, and where is the dispute? | A12 finds T9004, then reads dispute D-55120 |
| What is the late payment fee, and how much did I spend on dining this month? | A13 and A14 run in parallel: $35 [POL-LATE-03] and $188.70 |
| How much did I spend this month and which credit card suits my spending? | A14 runs first ($1,525.84), then A11 recommends the Flat 2% Cash Back Card |
| Am I over budget anywhere? | A14 `budget_status`: travel is over its $400 budget |
| What is the foreign transaction fee? | A13 cites [POL-FEES-02] |

What it can answer about is set by the files in `data/`. Edit them and restart to change the demo's world.

## The HTTP API

Interactive docs are at http://localhost:8000/docs.

### Ask a question (platform picks the agents)

```bash
curl -s localhost:8000/v1/chat -H 'content-type: application/json' -d '{
  "customer_id": "C1001",
  "message": "What is the late payment fee, and how much did I spend on dining this month?"
}'
```

Response (trimmed):

```json
{
  "session_id": "sess_…",
  "trace_id": "trace_…",
  "status": "ok",
  "path": "full",
  "response": "- Policy Knowledge Base: A $35 late fee applies … [POL-LATE-03]\n- Personal Finance (PFM): You spent $188.70 on dining in the last 30 days.",
  "context": {
    "intents":  [{"agent_id": "A13", "sub_query": "What is the late payment fee", "score": 0.9},
                 {"agent_id": "A14", "sub_query": "how much did I spend on dining this month", "score": 0.7}],
    "agents":   [{"agent_id": "A13", "status": "ok", "attempts": 1},
                 {"agent_id": "A14", "status": "ok", "attempts": 1}],
    "tools_used": ["search_policies", "spending_summary"],
    "orchestrator_loops": 1,
    "llm_calls": 6,
    "llm_calls_by_step": {"intent": 1, "agent": 4, "collate": 1},
    "cost_usd": 0.0,
    "latency_ms": 6
  }
}
```

On Claude, `context` also has `budget_remaining_usd`, and `cost_usd` is the real cost of this question.

To continue a conversation, send back the `session_id` you got. A session belongs to one customer; using it with
another `customer_id` returns 403.

### Ask with chosen agents (manual mode)

```bash
curl -s localhost:8000/v1/chat/manual -H 'content-type: application/json' -d '{
  "customer_id": "C1001",
  "message": "What is the late payment fee, and how much did I spend on dining this month?",
  "agent_ids": ["A13", "A14"]
}'
```

Same response shape, with `path: "manual"`. An empty `agent_ids` returns status `error`
("Select at least one agent before asking.") without calling an LLM.

### Status and path values

| `status` | Meaning |
| --- | --- |
| `ok` | answered |
| `partial` | some agents answered; the answer says which part couldn't be completed |
| `clarify` | ambiguous; `response` is the clarifying question; no agent ran |
| `out_of_scope` | not something the agents handle |
| `budget_exceeded` | the in-app spend cap stopped the request (see [cost.md](cost.md)) |
| `error` | key rejected, API unreachable, call cap reached, or a bad request; `response` says which |

| `path` | Meaning |
| --- | --- |
| `intent` | answered at the routing step (clarify, out of scope, budget stop) |
| `full` | ran the orchestration graph |
| `manual` | ran the graph with user-chosen agents |

### Other endpoints

| Method and path | Returns |
| --- | --- |
| `GET /v1/audit/{trace_id}` | every audit event for one request: routing, scores, each tool call, the answer |
| `GET /v1/sessions/{session_id}` | the conversation history |
| `GET /v1/agents` | each agent's id, name, keywords, tools and data files |
| `GET /v1/usage` | the spend ledger against the budget (Claude mode) |
| `GET /health` | provider, agent ids, and on Claude the models and budget left |

In this demo `customer_id` comes in the request body. In production it must come from the caller's auth token.

## demo.py

```bash
python demo.py                                  # the four demo questions
python demo.py "Do I have any pending charges?" # your own questions, one per argument
```

For each question it prints the route, status, agents, tools, LLM calls, cost, the answer and the trace id. On
Claude it ends with the total spent against the budget.

## Reading an audit trail

Every request writes events under one `trace_id`: the routing decision, the score for each agent, the
orchestrator steps, every tool call with its arguments, and the final response. Card numbers, SSNs and emails are
redacted before they are written. Open the "audit trail" link on the page, or call `GET /v1/audit/{trace_id}`.
Events are also appended to `audit.jsonl`.
