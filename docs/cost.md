# Cost and spend protection

The platform calls a paid LLM (Claude) only when `ANTHROPIC_API_KEY` is set. Without a key, or with
`LLM_PROVIDER=mock`, every call goes to the free offline mock and costs $0. Tests and CI always use the mock.

## Prices used

Prices are per million tokens and live in one table, `PRICES_PER_MTOK` in `app/llm/budget.py`. Update that table
when prices change.

| Model | Used for (default) | Input | Output |
| --- | --- | --- | --- |
| `claude-haiku-5-5` | routing (`INTENT_MODEL`) | $0.10 | $0.50 (prompts up to 100k tokens) |
| `claude-sonnet-5-5` | agents and collator (`AGENT_MODEL`, `COLLATE_MODEL`) | $2.00 | $10.00 |
| `claude-opus-5-5` | not used by default | $4.00 | $20.00 |
| any other model id | | $10.00 | $50.00 |

Unknown models are priced high on purpose, so the budget guard errs on the safe side. Prices are Anthropic's
first-party API rates as of November 2026.

Thinking tokens are billed as output tokens. They are included in each response's `usage.output_tokens`, so the
ledger counts them.

## What a question costs

These are **estimates**, from measured prompt sizes (about 800 tokens for the router, 120–550 tokens per agent
call, plus a few hundred tokens the API adds when tools are sent) and typical answer lengths. Your real numbers are
in the ledger after the first run.

| Question shape | LLM calls | Estimated cost |
| --- | --- | --- |
| Ambiguous or out of scope ("What about my card?") | 1 Haiku | about $0.0002 |
| One agent, two tools (the $89.99 dispute question) | 1 Haiku + 3 Sonnet | about $0.007–0.015 |
| Two agents, one tool each (late fee + dining spend) | 1 Haiku + 5 Sonnet | about $0.01–0.025 |
| One agent depends on another (spend + card advice) | 1 Haiku + 5 Sonnet | about $0.01–0.025 |

Longer answers or more thinking between tool calls can push a two-agent question to about $0.06. Running the four
demo questions once costs about $0.03–0.07. **A $5 budget covers roughly 200–500 two-agent questions.**

Almost all of the cost is Sonnet output. Routing on Haiku is close to free.

## Two layers of protection

### 1. Console workspace limit (authoritative)

This is the cap that actually protects the key, on every machine it is used from.

1. In the Claude Console, create a dedicated workspace for this project.
2. Set the workspace's monthly spend limit (for example $5).
3. Create the API key **inside that workspace**.
4. If you share the key for a demo, send it privately and revoke it afterwards.

Once the limit is reached the API refuses calls, and the app answers "rate or spend limit reached".

### 2. In-app budget guard

`BudgetGuard` (`app/llm/budget.py`) runs before every paid call:

- **Reserve the worst case first.** It estimates input at 3 characters per token and assumes the full
  `LLM_MAX_TOKENS` (2,048) of output. If `spent + reserved + this call` could exceed `BUDGET_USD`, the call is
  refused and never reaches the API.
- **Settle to the real cost.** After the call, the reservation is replaced by the actual cost from the response's
  `usage` block. On an error the reservation is released, so a rejected key records $0.
- **Persist.** Actual spend is written to `.usage/ledger.json` and reloaded at start-up, so restarting doesn't
  reset it.

When the guard stops a request, the response has status `budget_exceeded` and no further API request is sent.

Worst-case reservations are small: about $0.02 per Sonnet call and about $0.001 per Haiku call. So the guard stops
with about $0.02 of the budget unused.

### Per-request call cap

`MAX_LLM_CALLS_PER_REQUEST` (default 12) caps the calls one question can make, retries included. At the worst-case
reservation that bounds any single question at about $0.25. A question that hits the cap returns status `error`
("This request needed more AI steps than allowed").

The in-app ledger is per machine and per copy of the repo. The Console limit is the one to rely on.

## Checking what you've spent

| Where | Shows |
| --- | --- |
| `context.cost_usd` in every response | this question's actual cost |
| `context.budget_remaining_usd` | budget left after this question |
| `context.llm_calls_by_step` | calls per step (intent, agent, collate) |
| `GET /v1/usage` | the whole ledger: total spent, calls, tokens, spend by model, limit and remaining |
| `GET /health` | the models in use and budget left |
| end of `python demo.py` | "Total spent so far: $X of $Y" |
| the comparison page header and answer facts | budget left, and each answer's cost |
| the Claude Console | the authoritative bill for the workspace |

Example `GET /v1/usage`:

```json
{
  "provider": "anthropic",
  "spent_usd": 0.0412,
  "calls": 22,
  "input_tokens": 15840,
  "output_tokens": 3315,
  "by_model": {
    "claude-haiku-5-5":  {"calls": 4,  "usd": 0.0008},
    "claude-sonnet-5-5": {"calls": 18, "usd": 0.0404}
  },
  "updated_at": "2026-11-27T10:30:00+00:00",
  "limit_usd": 5.0,
  "remaining_usd": 4.9588
}
```

(Illustrative numbers. In mock mode `/v1/usage` says there are no paid calls.)

To reset the in-app ledger, stop the app and delete `.usage/ledger.json`. That only resets the local count; it
doesn't change what the Console has billed.

## Ways to spend less

| Lever | How | Trade-off |
| --- | --- | --- |
| Develop on the mock | `LLM_PROVIDER=mock` | answers are rule-based, not Claude |
| Lower the budget for demos | `BUDGET_USD=1` | stops sooner |
| Run agents on Haiku | `AGENT_MODEL=claude-haiku-5-5`, `COLLATE_MODEL=claude-haiku-5-5` | about 20× cheaper per token; weaker tool use and writing |
| Fewer tool rounds | `AGENT_MAX_TOOL_STEPS=3` | an agent may stop before finishing |
| Tighter call cap | `MAX_LLM_CALLS_PER_REQUEST=8` | complex questions may return `error` |
| Smaller output cap | `LLM_MAX_TOKENS=1024` | smaller reservations; long answers may be cut off |
| Plan cache (planned) | not built yet | would cut a two-agent question from 6 calls to 2 |

Prompt caching wouldn't help yet: these prompts are smaller than the minimum size the API caches.

## Known issue

Don't set `INTENT_MODEL` to `claude-sonnet-5-5` or `claude-opus-5-5`. The provider changes the forced router tool
to `tool_choice: any`, which those models reject, so every routed question fails. A rejected request isn't billed,
but nothing gets answered. Keep routing on Haiku until this is fixed.
