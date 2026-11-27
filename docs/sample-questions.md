# Sample questions

Questions to try on the comparison page (`make dev`, then http://localhost:5173), as customer **C1001**. Tick
the agents in the **Tick** column on the left ("Before") side; the right ("After") side picks them itself.

For the cache rows, click **Clear** next to "Cached plans" in the header first, then ask the questions in order:
the "Ask first" question saves a plan, and the next one reuses it.

Wording changes from run to run because every answer is a real Claude response. The figures come from the data in
`data/`, so they stay the same.

## One agent

| Question | Tick | What you should see |
| --- | --- | --- |
| Do I have any pending charges? | A12 | Sushi Bar, $74.10 on Nov 13 |
| Am I over budget anywhere? | A14 | travel: $644.50 against a $400 budget |
| How long do I have to dispute a charge? | A13 | 60 days from the statement date [POL-DISPUTE-01] |

## Two agents (in parallel)

| Question | Tick | What you should see |
| --- | --- | --- |
| What is the late payment fee, and how much did I spend on dining this month? | A13, A14 | $35 [POL-LATE-03] and $188.70. Labeled **Full flow**, "Plan saved" |
| How much did I spend on groceries this month, and what's the foreign transaction fee? | A14, A13 | $392.65 and 3% [POL-FEES-02]. **Full flow**, "Plan saved" |
| Is my Online Store 24 charge disputed, and how long do disputes usually take? | A12, A13 | case D-55120 under review, plus the 60-day / 10-business-day policy |

## Re-run using the cache

| Ask first | Then ask | Tick | What you should see |
| --- | --- | --- | --- |
| What is the late payment fee, and how much did I spend on dining this month? | How much did I spend on travel this month, and what's the late fee? | A13, A14 | **From cache**, below the first answer: $644.50 and $35. 2 LLM calls instead of 6, about half the time, a third of the cost |
| What is the late payment fee, and how much did I spend on dining this month? | How much have I spent on dining this month and what's the late fee? | A13, A14 | **From cache**: the same facts ($35, $188.70) from a reworded question |
| How much did I spend on groceries this month, and what's the foreign transaction fee? | How much did I spend on shopping this month, and what's the foreign transaction fee? | A14, A13 | **From cache**: $299.99 and 3%, with the same plan and a new category |

After any cached answer, press **More details** to re-run it through the full flow for a fuller, itemised answer.

## A query with no cache entry

| Question | Tick | What you should see |
| --- | --- | --- |
| What's the late fee and how much did I spend this month? (after the dining question) | A13, A14 | **Full flow**. Same agents, but no spending category, so the saved plan doesn't fit and the cache won't guess. Answer: $35 and $1,525.84 in total |
| What's the late fee, and am I over budget on anything? | A13, A14 | **Full flow**. This needs `budget_status` rather than `spending_summary`, so no saved plan matches. Answer: $35, plus travel over budget |
| What about my card? | A11 | No agent runs on the right: the platform asks whether you mean recommendations, transactions or policies. The left side answers from A11 only, showing what guessing looks like |

## What has been checked on Claude

The first two cache rows, the first two-agent row and the ambiguous question have been run on real Claude
(`scripts/live_check.py` checks them). The groceries / shopping pair follows the same cache rules but hasn't been
run live.
