# Agents and tools

Four agents, seven tools. The definitions live in `app/agents/` (one module per agent); `GET /v1/agents` returns
the same catalog from a running server.

No tool takes a customer id: the platform supplies it through `ToolContext`, so the model can never ask for another
customer's data. Inputs below are the arguments the LLM chooses; tools reject unknown or missing arguments and values
outside an enum.

| Agent | Tool | What it does | Inputs (from the LLM) | Returns | Data it reads |
| --- | --- | --- | --- | --- | --- |
| **A11** Credit Card Recommendation | `get_customer_profile` | Gets the customer's credit band, income band and existing cards | none | credit band, income band, existing cards | `customers.json` |
| | `recommend_cards` | Ranks eligible cards by estimated yearly rewards on the last 30 days of spending, after annual fees; skips cards the customer already holds | `query`, `top_n` (default 2) | ranked cards with yearly value, fee and intro offer | `customers.json`, `card_products.json`, `transactions.csv` |
| **A12** Transaction Queries | `search_transactions` | Finds the customer's recent transactions by merchant, category or status (pending, disputed) | `query`, `category` (dining, groceries, travel, shopping), `days` (default 30) | matching transactions (newest 20 at most), count, total | `transactions.csv` |
| | `get_dispute_status` | Gets the status of a disputed transaction | `transaction_id` (optional: finds the customer's dispute if left out), `query` | case id, stage, provisional credit, expected days | `disputes.json`, `transactions.csv` |
| **A13** Policy Knowledge Base | `search_policies` | Searches the policy documents paragraph by paragraph and returns passages to cite | `query` (required), `top_k` (default 2) | best passage per policy, with its `[POL-…]` id | `policies/*.md` |
| **A14** Personal Finance | `spending_summary` | Totals the last 30 days of spending, overall or for one category, and names the biggest category | `query`, `category` (dining, groceries, travel, shopping) | total, by-category breakdown, biggest category | `transactions.csv` |
| | `budget_status` | Compares this month's spending with the customer's budget for each category | `query` | spent vs budget per category, the over-budget list | `transactions.csv`, `customers.json` |

## Rules every tool follows

- **Own tools only.** An agent can call only the tools in its own list; a call to any other tool returns an error to
  that agent.
- **Own data only.** Each agent declares its data sources, and its tools read data only through a view of those
  sources. Reading any other source raises `SourceDenied`, which the agent sees as a tool error.
- **Bounded output.** Tools return totals and summaries, plus at most 20 rows, so the prompt and its cost stay the
  same size however much data a customer has.
- **A one-sentence `summary`** is in every tool's output, with amounts formatted as `$1,234.56`.
- **Errors go back to the agent** as `{"error": ...}`, never raised to the platform.
- **MCP-shaped specs** (`name`, `description`, `input_schema`), so each agent's tools could be served by its own MCP
  server without changes.

## What the plan cache reuses

A saved plan records which tool each agent ran and with which arguments. `query` is re-bound to the new question's
sub-query; values that came from the question, such as `category`, become slots filled from the new question; only
`top_n` and `top_k` are kept as they are, because they're settings, not customer data (each tool lists these in
`replay_args`). See [architecture.md](architecture.md#plan-cache).

## Adding an agent (A15)

Add one module in `app/agents/` with its tools (`@tool(...)`) and a `build()` that returns an `Agent` with its
`sources`. If it needs a new data file, add the file under `data/` and its repository to `SOURCES` in
`app/data/repositories.py`. Append the builder to `AGENT_BUILDERS` in `app/agents/registry.py`. The router picks it
up from the catalog automatically, and adding it changes the catalog version, so older cached plans are no longer
reused.
