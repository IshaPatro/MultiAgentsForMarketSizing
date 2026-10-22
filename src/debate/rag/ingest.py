"""PDF cleaning helpers and the embedding step. The corpus structure itself lives in debate.rag.corpus."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from debate.config import ROOT, CorpusManifest
from debate.rag.embeddings import Embedder

MIN_EXHIBIT_WORDS = 25

# Licence / layout furniture that must never become evidence.
_DROP_LINE = re.compile(
    r"^(For the exclusive use of .*|This document is authorized for use only.*|821-045|9-821-045|\d{1,2}|"
    r"Biobot Analytics|R E V :.*|R A Y M O N D.*|J O S H U A.*|M I T C H E L L.*)$", re.I)
_DROP_BLOCK = re.compile(r"^(Professors .* prepared this case|Copyright ©)", re.I)
_ACCESSED = re.compile(r",?\s*accessed\s+[A-Z][a-z]+\s+\d{4}\.?", re.I)
_URL = re.compile(r"https?://\S+(?:\s*-\s*\d\d/\S+)?")  # incl. URLs a line wrap split mid-path ("2020- 08/x.pdf")
_ENDNOTE_REF = re.compile(r"(?<=[.”\"’)])\s+\d{1,2}(?=\s+[A-Z“\"(])")


@dataclass
class RawChunk:
    chunk_id: str
    page: int
    section: str
    doc_type: str
    text: str


def clean_block(raw: str) -> str:
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln and not _DROP_LINE.match(ln)]
    text = " ".join(lines)
    if _DROP_BLOCK.match(text):
        return ""
    text = _ACCESSED.sub("", _URL.sub("", text))
    text = _ENDNOTE_REF.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_heading(text: str) -> bool:
    words = text.split()
    return 0 < len(words) <= 8 and not text.endswith((".", ",", ";", ":", "”", '"')) and text[0].isupper()


def page_paragraphs(page: pymupdf.Page) -> list[str]:
    out = []
    for b in sorted(page.get_text("blocks"), key=lambda b: (round(b[1]), b[0])):
        if b[6] != 0:
            continue
        text = clean_block(b[4])
        if text:
            out.append(text)
    return out


def chunk_exhibit_page(paragraphs: list[str], page: int, source_id: str, company_id: str) -> list[RawChunk]:
    """Exhibits are tables and captions: one chunk per page, never split."""
    if len(" ".join(paragraphs).split()) < MIN_EXHIBIT_WORDS:  # image-only pages leave a bare caption
        return []
    m = re.match(r"(Exhibit \d+)\s*(.*)", paragraphs[0])
    section = f"{m.group(1)}: {m.group(2)}".strip(": ") if m else "Exhibit"
    return [RawChunk(f"{company_id}_{source_id}_p{page}_c1", page, section, "exhibit", " ".join(paragraphs))]


def ingest(manifest: CorpusManifest, conn, embedder: Embedder, root: Path = ROOT) -> dict:
    """Embed the validated corpus (see debate.rag.corpus.load_chunks) and replace the company's rows."""
    from debate.rag.corpus import load_chunks

    chunks = load_chunks(manifest, root)
    titles = {**manifest.topics, "sources": "Sources cited by the case"}
    # Contextual header: the topic and chunk type travel with the text into both indexes, so a question about
    # "funding" finds a fact that only says "grant", and agents see what kind of evidence they are reading.
    texts = [f"{titles.get(c.topic, c.topic)} | {c.doc_type.replace('_', ' ')}: {c.text}" for c in chunks]
    vectors = embedder.embed_docs(texts)
    rows = [(c.chunk_id, manifest.company_id, c.source_id, c.source_id, c.doc_type, c.page, c.section, text,
             c.topic, c.as_of, json.dumps(c.meta, default=str), embedder.name, vec)
            for c, text, vec in zip(chunks, texts, vectors)]
    with conn.transaction():
        # Replace the whole company corpus: a source removed from the manifest must not stay retrievable.
        conn.execute("DELETE FROM chunks WHERE company_id=%s", (manifest.company_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (chunk_id, company_id, source_id, source, doc_type, page, section, text,"
                " topic, as_of, meta, embedding_model, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
    return {"chunks": len(chunks), "by_type": by_type, "words": sum(len(c.text.split()) for c in chunks)}
