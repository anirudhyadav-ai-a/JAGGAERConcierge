.PHONY: install test demo serve frontend-install frontend-dev frontend-build frontend-test dev

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
