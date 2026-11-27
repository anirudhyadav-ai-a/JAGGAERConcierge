.PHONY: install test demo serve frontend-install frontend-dev frontend-build frontend-test dev live-check scale-check

install:
	python3 -m pip install -r requirements.txt

test:
	LLM_PROVIDER=mock python3 -m pytest -q

demo:
	python3 demo.py

# Backend API (and the original page at /, plus the built React app at /app/ if built)
serve:
	python3 -m uvicorn app.main:app --reload --port 8000

# React frontend (frontend/)
frontend-install:
	cd frontend && npm ci

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-test:
	cd frontend && npm test

# Backend on :8000 and the React dev server on :5173, together. Ctrl+C stops both.
dev:
	$(MAKE) -j2 serve frontend-dev

# Real Claude: the four interview questions, with fact checks (needs ANTHROPIC_API_KEY, costs a few cents)
live-check:
	python3 scripts/live_check.py --max-usd 0.30

# Free: time the tools on a million generated transactions
scale-check:
	python3 scripts/scale_check.py
