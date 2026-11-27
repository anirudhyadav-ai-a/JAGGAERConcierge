"""Slot extraction for the plan cache, by rules in code (the same for Claude and the test mock).

A cached plan never stores a value from the question it was built from. Values that differ between
questions ("dining" vs "travel", a merchant name, a transaction id, a time window) become named slots:
{category}, {merchant}, {transaction_id}, {days}, {amount}. A later question fills the slots with its own values.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

CATEGORIES = ("dining", "groceries", "travel", "shopping")
_CATEGORY_WORDS = {
    "dining": "dining", "restaurant": "dining", "restaurants": "dining", "eating out": "dining",
    "groceries": "groceries", "grocery": "groceries",
    "travel": "travel", "flights": "travel", "flight": "travel",
    "shopping": "shopping",
}
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "twelve": 12}


def _days(text: str) -> int | None:
    t = text.lower()
    m = re.search(r"\b(?:last|past)\s+(\d+|\w+)\s+(day|week|month)s?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUMBERS.get(m.group(1))
        if n:
            return n * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    if re.search(r"\b(this|past|last)\s+week\b", t):
        return 7
    if re.search(r"\b(this|past|last)\s+month\b|\bmonthly\b|\b30 days\b", t):
        return 30
    if re.search(r"\b(this|past|last)\s+year\b", t):
        return 365
    return None


def extract_entities(text: str, merchants: Iterable[str] = ()) -> dict[str, Any]:
    """Slot values found in the question: category, merchant, transaction_id, days, amount."""
    low = text.lower()
    found: dict[str, Any] = {}
    for word, cat in sorted(_CATEGORY_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", low):
            found["category"] = cat
            break
    for name in sorted(merchants, key=len, reverse=True):
        if name and re.search(rf"\b{re.escape(name.lower())}\b", low):
            found["merchant"] = name
            break
    if m := re.search(r"\bT\d{3,}\b", text):
        found["transaction_id"] = m.group(0)
    if (d := _days(text)) is not None:
        found["days"] = d
    if m := re.search(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)", text):
        found["amount"] = float(m.group(1).replace(",", ""))
    return found


def mask(text: str, entities: dict[str, Any]) -> str:
    """The question with its slot values replaced by slot names: what the cache compares and stores."""
    t = " " + text.lower() + " "
    if "merchant" in entities:
        t = re.sub(rf"\b{re.escape(str(entities['merchant']).lower())}\b", " {merchant} ", t)
    t = re.sub(r"\bt\d{3,}\b", " {transaction_id} ", t)
    t = re.sub(r"\$\s?\d[\d,]*(?:\.\d{1,2})?", " {amount} ", t)
    t = re.sub(r"\b(?:(?:last|past)\s+(?:\d+|\w+)\s+(?:day|week|month)s?|(?:this|past|last)\s+(?:week|month|year)|30 days)\b",
               " {days} ", t)
    for word in sorted(_CATEGORY_WORDS, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(word)}\b", " {category} ", t)
    return " ".join(t.split())
