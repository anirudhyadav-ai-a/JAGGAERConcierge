"""Data sources under data/, one repository class per source.

Each repository reads one file (or folder) from the data directory. To move to a real system of record,
replace one class (core banking API, card catalog service, policy store...) and keep its methods.

Agents never import these directly. The registry gives each agent a SourceView that exposes only the
sources the agent declares, so an agent cannot read another domain's data by accident.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path


class CustomerRepository:
    """data/customers.json: profile, credit band, existing cards, monthly budget."""

    def __init__(self, path: Path):
        self._by_id = {c["customer_id"]: c for c in json.loads(path.read_text())}

    def get(self, customer_id: str) -> dict | None:
        c = self._by_id.get(customer_id)
        return dict(c) if c else None


class TransactionRepository:
    """data/transactions.csv: one row per card transaction."""

    def __init__(self, path: Path):
        self._by_customer: dict[str, list[dict]] = {}
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                cid = row.pop("customer_id")
                row["amount"] = float(row["amount"])
                self._by_customer.setdefault(cid, []).append(row)

    def for_customer(self, customer_id: str, since: str | None = None) -> list[dict]:
        rows = self._by_customer.get(customer_id, [])
        return [dict(t) for t in rows if since is None or t["date"] >= since]


class DisputeRepository:
    """data/disputes.json: dispute cases keyed by transaction id."""

    def __init__(self, path: Path):
        self._by_txn = {d["transaction_id"]: d for d in json.loads(path.read_text())}

    def get(self, transaction_id: str) -> dict | None:
        d = self._by_txn.get(transaction_id)
        return {k: v for k, v in d.items() if k != "transaction_id"} if d else None

    def transaction_ids(self) -> list[str]:
        return list(self._by_txn)


class CardProductRepository:
    """data/card_products.json: card catalog with reward rates (% back by category)."""

    def __init__(self, path: Path):
        self._cards = json.loads(path.read_text())

    def all(self) -> list[dict]:
        return [dict(c) for c in self._cards]


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


class PolicyRepository:
    """data/policies/*.md: one markdown file per policy, with `id` and `title` in front matter.

    Each document is split into paragraph chunks so retrieval returns the passage that answers
    the question, not the whole document.
    """

    def __init__(self, folder: Path):
        self._docs = [self._parse(p) for p in sorted(folder.glob("*.md"))]

    @staticmethod
    def _parse(path: Path) -> dict:
        raw = path.read_text()
        m = _FRONT_MATTER.match(raw)
        if not m:
            raise ValueError(f"{path.name}: missing front matter with id and title")
        meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
        meta = {k.strip(): v.strip() for k, v in meta.items()}
        body = raw[m.end():].strip()
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]
        return {"id": meta["id"], "title": meta["title"], "file": path.name, "paragraphs": paragraphs}

    def documents(self) -> list[dict]:
        return [dict(d) for d in self._docs]

    def chunks(self) -> list[dict]:
        return [{"id": d["id"], "title": d["title"], "chunk": i, "text": text}
                for d in self._docs for i, text in enumerate(d["paragraphs"])]


# source name -> (repository class, path inside the data directory)
SOURCES = {
    "customers": (CustomerRepository, "customers.json"),
    "transactions": (TransactionRepository, "transactions.csv"),
    "disputes": (DisputeRepository, "disputes.json"),
    "card_products": (CardProductRepository, "card_products.json"),
    "policies": (PolicyRepository, "policies"),
}


class DataCatalog:
    """Loads each source from the data directory the first time an agent needs it."""

    def __init__(self, data_dir: str | Path, today: date):
        self.data_dir = Path(data_dir)
        self.today = today
        self._repos: dict[str, object] = {}

    def repo(self, name: str):
        if name not in SOURCES:
            raise KeyError(f"unknown data source {name!r}; known: {sorted(SOURCES)}")
        if name not in self._repos:
            cls, rel = SOURCES[name]
            path = self.data_dir / rel
            if not path.exists():
                raise FileNotFoundError(f"data source {name!r} expects {path}")
            self._repos[name] = cls(path)
        return self._repos[name]

    def view(self, agent_id: str, sources: list[str]) -> "SourceView":
        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            raise KeyError(f"{agent_id} declares unknown data sources {unknown}")
        return SourceView(self, agent_id, tuple(sources))


class SourceDenied(PermissionError):
    pass


class SourceView:
    """What one agent's tools see: only the sources that agent declared, plus `today` and `since()`."""

    def __init__(self, catalog: DataCatalog, agent_id: str, allowed: tuple[str, ...]):
        self._catalog = catalog
        self.agent_id = agent_id
        self.allowed = allowed

    @property
    def today(self) -> date:
        return self._catalog.today

    def since(self, days: int) -> str:
        return (self.today - timedelta(days=days)).isoformat()

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in SOURCES:
            raise AttributeError(name)
        if name not in self.allowed:
            raise SourceDenied(f"agent {self.agent_id} has no access to data source {name!r}")
        return self._catalog.repo(name)

    @cached_property
    def files(self) -> list[str]:
        return [SOURCES[s][1] for s in self.allowed]
