# React frontend

The before/after comparison page as a React app (Vite + TypeScript). It talks to the FastAPI backend in the
parent folder; it has no backend of its own.

```bash
npm ci            # install
npm run dev       # http://localhost:5173, with /v1 and /health passed through to http://localhost:8000
npm test          # component and logic tests (Vitest + Testing Library)
npm run build     # type-checks and builds into dist/, which the backend serves at /app/
```

Set `API_TARGET` to point the dev server at a backend on another host or port.

| File | Does |
| --- | --- |
| `src/api.ts` | typed calls to `/health`, `/v1/agents`, `/v1/chat`, `/v1/chat/manual` |
| `src/compare.ts` | the comparison rules and status labels |
| `src/App.tsx` | state: sessions per side, the agent selection, asking both sides in parallel |
| `src/components/` | Header, AskBar, ComparisonStrip, AgentPicker, Turn |

See [docs/running.md](../docs/running.md) for running it with the backend.
