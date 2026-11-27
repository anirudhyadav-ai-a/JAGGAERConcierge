"""Tests never call the paid API: the whole suite runs on the deterministic mock, or on the fake Claude client
in tests/fake_anthropic.py. Set before app modules are imported, because app.main builds the platform on import."""
import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ["CACHE_PATH"] = ""          # plan cache in memory: each platform starts empty, nothing written to disk
os.environ.pop("ANTHROPIC_API_KEY", None)
