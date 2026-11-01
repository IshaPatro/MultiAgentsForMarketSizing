"""Turn statistics. Answers are not length-limited (the prompts ask for precision); word counts are only recorded."""
from __future__ import annotations


def word_count(text: str) -> int:
    return len(text.split())
