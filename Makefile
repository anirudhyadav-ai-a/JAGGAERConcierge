.PHONY: install test demo serve

install:
	python3 -m pip install -r requirements.txt

test:
	LLM_PROVIDER=mock python3 -m pytest -q

demo:
	python3 demo.py

serve:
	python3 -m uvicorn app.main:app --reload --port 8000
