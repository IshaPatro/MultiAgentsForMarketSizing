"""Brevity guard (implementation.md section 6, layers 3-4). Pure code, no model call."""
from __future__ import annotations

import re


def word_count(text: str) -> int:
    return len(text.split())


def trim_to_words(text: str, limit: int) -> str:
    """Last resort: cut at the last sentence end within the limit, else at the word limit."""
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    head = " ".join(words[:limit])
    last_end = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
    if head.endswith((".", "?", "!")):
        return head
    if last_end > len(head) // 2:
        return head[: last_end + 1]
    return re.sub(r"[,;:\s]+$", "", head) + "..."
