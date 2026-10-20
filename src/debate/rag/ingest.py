"""PDF -> clean paragraphs -> ~350-word chunks -> dated -> embedded -> pgvector."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf

from debate.config import ROOT, CorpusManifest, CorpusSegment, CorpusSource, EraContext, TierConfig
from debate.llm.client import ModelClient
from debate.rag.dating import DateDecision, date_chunk
from debate.rag.embeddings import Embedder

TARGET_WORDS = 350     # ~500 tokens
OVERLAP_WORDS = 50     # ~15%
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


def chunk_narrative(paragraphs: list[tuple[int, str]], source_id: str, company_id: str, doc_type: str) -> list[RawChunk]:
    """Greedy paragraph packing with a word overlap carried into the next chunk; headings stay with their text."""
    chunks: list[RawChunk] = []
    section, buf, buf_words, buf_page = "", [], 0, 0  # buf_page: page of the first NEW paragraph (0 = unset)
    counters: dict[int, int] = {}

    def flush():
        nonlocal buf, buf_words, buf_page
        if not buf:
            return
        text = " ".join(buf)
        n = counters[buf_page] = counters.get(buf_page, 0) + 1
        chunks.append(RawChunk(f"{company_id}_{source_id}_p{buf_page}_c{n}", buf_page, section, doc_type, text))
        tail = " ".join(text.split()[-OVERLAP_WORDS:])
        buf, buf_words, buf_page = [tail], len(tail.split()), 0

    for page, para in paragraphs:
        if _is_heading(para):
            if buf_words > OVERLAP_WORDS:
                flush()
            section = para
            continue
        n_words = len(para.split())
        if buf_words + n_words > TARGET_WORDS and buf_words > OVERLAP_WORDS:
            flush()
        if not buf_page:
            buf_page = page
        buf.append(para)
        buf_words += n_words
    if buf_words > OVERLAP_WORDS or (buf and not chunks):
        flush()
    return chunks


def chunk_exhibit_page(paragraphs: list[str], page: int, source_id: str, company_id: str) -> list[RawChunk]:
    """Exhibits are tables and captions: one chunk per page, never split."""
    if len(" ".join(paragraphs).split()) < MIN_EXHIBIT_WORDS:  # image-only pages leave a bare caption
        return []
    m = re.match(r"(Exhibit \d+)\s*(.*)", paragraphs[0])
    section = f"{m.group(1)}: {m.group(2)}".strip(": ") if m else "Exhibit"
    return [RawChunk(f"{company_id}_{source_id}_p{page}_c1", page, section, "exhibit", " ".join(paragraphs))]


def extract_chunks(source: CorpusSource, company_id: str, root: Path = ROOT) -> tuple[list[RawChunk], list[str]]:
    doc = pymupdf.open(root / source.path)
    chunks: list[RawChunk] = []
    skipped: list[str] = []
    for seg in source.segments:
        first, last = seg.pages
        if seg.skip:
            skipped.append(f"pages {first}-{last}: {seg.reason}")
            continue
        if seg.doc_type == "exhibit":
            for pno in range(first, last + 1):
                chunks += chunk_exhibit_page(page_paragraphs(doc[pno - 1]), pno, source.id, company_id)
        else:
            paras = [(pno, p) for pno in range(first, last + 1) for p in page_paragraphs(doc[pno - 1])]
            chunks += chunk_narrative(paras, source.id, company_id, seg.doc_type)
    return chunks, skipped


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def decide_date(chunk: RawChunk, manifest: CorpusManifest, llm: ModelClient | None, tier: TierConfig | None) -> DateDecision:
    """A reviewed date applies only to the text that was reviewed; anything else goes through the date check."""
    approved = manifest.approved_dates.get(chunk.chunk_id)
    if approved and approved.text_sha256 == text_fingerprint(chunk.text):
        return DateDecision(approved.as_of, False, "human-reviewed date")
    d = date_chunk(chunk.text, manifest.default_as_of, llm, tier)
    if approved:  # stale approval: never trust it, always send the chunk back to review
        d.needs_review = True
        d.note = f"approved date is stale: text changed since review (now {text_fingerprint(chunk.text)}). {d.note}".strip()
    return d


def era_tags_for(as_of: date | None, eras: dict[str, EraContext]) -> list[str]:
    if as_of is None:
        return []
    return [name for name, era in eras.items() if as_of <= era.cutoff_date]


def ingest(manifest: CorpusManifest, conn, embedder: Embedder, eras: dict[str, EraContext],
           llm: ModelClient | None = None, date_tier: TierConfig | None = None,
           review_csv: Path | None = None) -> dict:
    rows, review, skipped_all = [], [], []
    seen_ids: set[str] = set()
    for source in manifest.sources:
        chunks, skipped = extract_chunks(source, manifest.company_id)
        skipped_all += skipped
        vectors = embedder.embed_docs([c.text for c in chunks])
        for c, vec in zip(chunks, vectors):
            seen_ids.add(c.chunk_id)
            d = decide_date(c, manifest, llm, date_tier)
            rows.append((c.chunk_id, manifest.company_id, source.id, source.title, c.doc_type, c.page, c.section,
                         c.text, d.as_of, era_tags_for(d.as_of, eras), d.needs_review, d.note, embedder.name, vec))
            if d.needs_review:
                review.append({"chunk_id": c.chunk_id, "page": c.page, "as_of_date": d.as_of or "",
                               "text_sha256": text_fingerprint(c.text), "note": d.note})
    unknown = set(manifest.approved_dates) - seen_ids
    if unknown:
        raise ValueError(f"approved dates refer to missing chunks: {', '.join(sorted(unknown))}")
    with conn.transaction():
        # Replace the whole company corpus: a source removed from the manifest must not stay retrievable.
        conn.execute("DELETE FROM chunks WHERE company_id=%s", (manifest.company_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (chunk_id, company_id, source_id, source, doc_type, page, section, text, as_of_date,"
                " era_tags, needs_review, review_note, embedding_model, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    if review_csv:
        review_csv.parent.mkdir(parents=True, exist_ok=True)
        with review_csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["chunk_id", "page", "as_of_date", "text_sha256", "note"])
            w.writeheader()
            w.writerows(review)
    return {"chunks": len(rows), "undated": sum(1 for r in rows if r[8] is None), "needs_review": len(review),
            "skipped": skipped_all, "words": sum(len(r[7].split()) for r in rows)}
