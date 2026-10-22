"""Structured, dated corpus built from the case PDF plus hand-curated files (implementation.md section 7).

    build_corpus(manifest)  PDF -> generated files in corpus_dir:
        narrative/NN_<topic>.md   one dated chunk per paragraph, grouped by topic, endnote markers resolved
        exhibits/<id>.csv         exhibit tables as rows (matrix: one row per segment; budget: one per line item)
        captions.yaml             figure exhibits (only captions are text)
        sources/endnotes.yaml     the case's endnotes, each with its own publication date
        sources/<id>.yaml         public research ledgers (manifest public_sources), one dated item per row/bullet
        README.md                 index, counts, Exhibit 7 reconciliation, sizing lineage
    load_chunks(manifest)   generated + curated files -> validated CorpusChunk list for ingestion

Curated (hand-written, never overwritten): facts.yaml (atomic facts, every quote verified against the PDF),
assumptions.yaml (inputs that are NOT in the case, optionally sourced from public ledger items), sizing.yaml (TAM
methods recomputed from facts/assumptions).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pymupdf
import yaml
from pydantic import BaseModel, Field, model_validator

from debate.config import DATE_PATTERN, ROOT, CorpusManifest, ExhibitSpec
from debate.numeric import check_calculation
from debate.rag.ingest import _DROP_BLOCK, _DROP_LINE, _is_heading, chunk_exhibit_page, clean_block

MIN_PARAGRAPH_WORDS = 40   # shorter paragraphs are merged with the next one on the same topic
MAX_CHUNK_WORDS = 380      # longer paragraphs are split at a sentence boundary
_DATE = re.compile(DATE_PATTERN)
_MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                         "September", "October", "November", "December"), 1)}

DocType = Literal["narrative", "fact", "exhibit_row", "exhibit_caption", "endnote", "assumption", "sizing_model",
                  "public_source", "public_lead"]


@dataclass
class CorpusChunk:
    chunk_id: str
    source_id: str        # case | fact | ex5 | ex6 | ex7 | caption | note | assume | sizing | public
    doc_type: str
    topic: str
    as_of: str            # YYYY[-MM[-DD]]: the date the information refers to
    text: str
    page: int | None = None
    section: str | None = None
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------------- endnote markers

# A citation marker is 1-2 digits glued to the end of a word or a closing quote/bracket ("investors.2",
# "1960s.4", "50.”39", "manholes43)", "articles.27,28,29,30"). "$3.6 million" must not match, hence a
# separate lookbehind for "<letter>." (or "<letter>," as in "commercially,31") rather than "." alone.
_NOTE_MARK = re.compile(r"(?:(?<=[A-Za-z%)”\"’])|(?<=[A-Za-z%)”\"’][.,]))(\d{1,2}(?:,\d{1,2})*)(?=[\s),;:]|$)")


def extract_note_markers(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Remove endnote markers that refer to real endnotes; return (clean text, note numbers in order)."""
    found: list[str] = []

    def repl(m: re.Match) -> str:
        nums = m.group(1).split(",")
        if all(n in known for n in nums):
            found.extend(n for n in nums if n not in found)
            return ""
        return m.group(0)

    return _NOTE_MARK.sub(repl, text), found


def normalize(text: str) -> str:
    """Comparable form of case text: straight quotes, plain dashes, no line-break hyphen gaps, single spaces."""
    text = (text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace("`", "'")
            .replace("–", "-").replace("—", "-"))
    text = re.sub(r"(\w)- (\w)", r"\1-\2", text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------------------------------------ narrative

@dataclass
class Paragraph:
    text: str
    pages: list[int]
    section: str
    notes: list[str] = field(default_factory=list)
    topic: str = ""


def _raw_paragraphs(page: pymupdf.Page) -> list[str]:
    """Paragraph blocks without licence furniture. Endnote markers are kept so they can be resolved."""
    out = []
    for b in sorted(page.get_text("blocks"), key=lambda b: (round(b[1]), b[0])):
        if b[6] != 0:
            continue
        lines = [ln.strip() for ln in b[4].splitlines()]
        text = " ".join(ln for ln in lines if ln and not _DROP_LINE.match(ln))
        if text and not _DROP_BLOCK.match(text):
            out.append(re.sub(r"\s+", " ", text).strip())
    return out


def read_paragraphs(doc: pymupdf.Document, manifest: CorpusManifest, known_notes: set[str]) -> list[Paragraph]:
    first, last = manifest.case.narrative_pages
    section, paras = "(intro)", []
    for pno in range(first, last + 1):
        for raw in _raw_paragraphs(doc[pno - 1]):
            if _is_heading(raw):
                section = raw
                continue
            # A paragraph cut by a page break continues with a lowercase word: stitch it back together.
            if paras and paras[-1].section == section and not paras[-1].text.rstrip().endswith(
                    (".", "?", "!", "”", '"', "’", ")", ":")) and raw[:1].islower():
                paras[-1].text += " " + raw
                paras[-1].pages.append(pno)
                continue
            paras.append(Paragraph(raw, [pno], section))
    for p in paras:
        p.text, p.notes = extract_note_markers(p.text, known_notes)
        p.text = re.sub(r"\s+([.,;:)])", r"\1", re.sub(r"\s+", " ", p.text)).strip()
    return paras


def assign_topics(paras: list[Paragraph], manifest: CorpusManifest) -> None:
    for p in paras:
        if p.section not in manifest.section_topics:
            raise ValueError(f"section {p.section!r} (p.{p.pages[0]}) has no topic in section_topics")
        p.topic = manifest.section_topics[p.section]
    for opening, topic in manifest.paragraph_topics.items():
        hits = [p for p in paras if normalize(p.text).startswith(normalize(opening))]
        if len(hits) != 1:
            raise ValueError(f"paragraph_topics key {opening!r} matches {len(hits)} paragraphs (needs exactly 1)")
        hits[0].topic = topic


def _years(text: str) -> list[int]:
    return sorted({int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", text)})


def _split_long(text: str, limit: int) -> list[str]:
    if len(text.split()) <= limit:
        return [text]
    parts, cur = [], []
    for sentence in re.split(r"(?<=[.!?”])\s+(?=[A-Z“\"])", text):
        if cur and len(" ".join(cur + [sentence]).split()) > limit:
            parts.append(" ".join(cur))
            cur = []
        cur.append(sentence)
    return parts + [" ".join(cur)] if cur else parts


def narrative_chunks(paras: list[Paragraph], manifest: CorpusManifest) -> list[CorpusChunk]:
    merged: list[Paragraph] = []
    for p in paras:
        prev = merged[-1] if merged else None
        if prev and len(prev.text.split()) < MIN_PARAGRAPH_WORDS and (prev.topic, prev.section) == (p.topic, p.section):
            prev.text += " " + p.text
            prev.pages = sorted(set(prev.pages + p.pages))
            prev.notes += [n for n in p.notes if n not in prev.notes]
        else:
            merged.append(Paragraph(p.text, list(p.pages), p.section, list(p.notes), p.topic))
    chunks, counters = [], {}
    for p in merged:
        for part in _split_long(p.text, MAX_CHUNK_WORDS):
            page = p.pages[0]
            n = counters[page] = counters.get(page, 0) + 1
            chunks.append(CorpusChunk(
                f"{manifest.company_id}_case_p{page}_c{n}", "case", "narrative", p.topic, manifest.case.as_of,
                part, page, p.section, {"pages": p.pages, "notes": p.notes, "years_mentioned": _years(part)}))
    return chunks


# ------------------------------------------------------------------------------------------------- endnotes

def _partial_date(text: str) -> str | None:
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                  r"\s+(\d{1,2}),\s+(\d{4})", text)
    if m:
        return f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                  r"\s+(\d{4})", text)
    if m:
        return f"{m.group(2)}-{_MONTHS[m.group(1)]:02d}"
    years = re.findall(r"[,(]\s*((?:19|20)\d\d)\s*[,.)]", text)
    return years[-1] if years else None


def parse_endnotes(doc: pymupdf.Document, manifest: CorpusManifest) -> list[dict]:
    first, last = manifest.case.endnote_pages
    notes: list[dict] = []
    for pno in range(first, last + 1):
        for raw in _raw_paragraphs(doc[pno - 1]):
            m = re.match(r"^(\d{1,2}) (.+)$", raw)
            if m:
                number = m.group(1)
                if any(n["number"] == number for n in notes):  # the case numbers two different notes "22"
                    number += "b"
                notes.append({"number": number, "text": m.group(2), "page": pno})
            elif notes and not re.fullmatch(r"\d{1,3}|Endnotes|.*821-045.*", raw):
                notes[-1]["text"] += " " + raw  # a note that continues on the next page
    for n in notes:
        text = re.sub(r"(https?://\S*-)\s+(\S)", r"\1\2", n["text"])  # URLs broken by a line wrap
        url = re.search(r"https?://\S+?(?=[,]?\s|$)", text)
        accessed = re.search(r"accessed\s+([A-Z][a-z]+\s+\d{4})", text)
        citation = re.sub(r",?\s*accessed\s+[A-Z][a-z]+\s+\d{4}", "", re.sub(r"https?://\S+", "", text))
        citation = re.sub(r"\s+([,.])", r"\1", re.sub(r"\s+", " ", citation)).rstrip(" ,")
        citation = re.sub(r"([.”\"])\.+$", r"\1", citation)  # "“Title.” ." left behind by a removed URL
        citation += "" if re.search(r"[.”\"]$", citation) else "."
        override = manifest.endnote_dates.get(n["number"].rstrip("b"))
        published = override or _partial_date(citation)
        n.update(citation=citation, url=url.group(0).rstrip(".") if url else None,
                 accessed=_partial_date(accessed.group(1)) if accessed else None,
                 published=published or (_partial_date(accessed.group(1)) if accessed else None),
                 date_basis="manifest" if override else "citation" if published else "accessed")
        del n["text"]
    return notes


# ------------------------------------------------------------------------------------------------- exhibits

def _cells(page: pymupdf.Page) -> list[list[str]]:
    """Table cells read by position, so words keep their spaces (plain table extraction drops them)."""
    tables = page.find_tables().tables
    if not tables:
        raise ValueError(f"no table found on p.{page.number + 1}")
    rows = []
    for r in tables[0].rows:
        rows.append([" ".join(page.get_text("text", clip=pymupdf.Rect(c)).split()) if c else "" for c in r.cells])
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def parse_matrix(rows: list[list[str]], spec: ExhibitSpec) -> list[dict]:
    """Segments are columns, attributes are rows; an attribute may wrap onto unlabeled continuation rows.

    Blocks repeat (Exhibit 5: public then private sector). A run of `header_lines` unlabeled rows followed by
    the block's first attribute label starts a new block; any other unlabeled row continues the attribute above.
    """
    rows = [r for r in rows if any(r)]
    records, i, block = [], 0, 0
    while i < len(rows):
        header = rows[i:i + spec.header_lines]
        i += spec.header_lines
        segments = [" ".join(h[c] for h in header if h[c]).strip() for c in range(1, len(rows[0]))]
        attrs: dict[str, list[str]] = {}
        first_attr, current = None, None
        while i < len(rows):
            r = rows[i]
            if r[0]:
                current = r[0]
                first_attr = first_attr or current
                attrs[current] = list(r[1:])
            elif (first_attr and i + spec.header_lines < len(rows)
                  and rows[i + spec.header_lines][0] == first_attr):
                break  # the next block's headings
            elif current:
                attrs[current] = [f"{a} {b}".strip() for a, b in zip(attrs[current], r[1:])]
            i += 1
        group = spec.groups[block] if block < len(spec.groups) else f"Block {block + 1}"
        for c, segment in enumerate(segments):
            if segment:
                records.append({"group": group, "segment": segment,
                                **{_slug(k): v[c].strip() for k, v in attrs.items()}})
        block += 1
    return records


_AMOUNT_OK = re.compile(r"^\d{1,3}(,\d{3})*$")


def parse_budget(rows: list[list[str]], spec: ExhibitSpec) -> tuple[list[dict], list[str]]:
    """Boston's budget table: line items grouped into bureaus, reconciled against every printed total.

    Returns (rows, reconciliation notes). A cell whose digit grouping is malformed is kept as printed and, when it
    is the only unknown in its column, its value is implied from the grand total.
    """
    header = rows[0]
    lowered = [h.lower() for h in header]
    col_index = {key: lowered.index(heading.lower()) for key, heading in spec.columns.items()}
    headings = {h.lower() for h in spec.columns.values()}
    label_col = next(i for i, h in enumerate(header) if h and h.lower() not in headings)

    def value(cell: str) -> tuple[int | None, str]:
        if cell in ("-", "–", ""):
            return 0, cell
        return (int(cell.replace(",", "")), cell) if _AMOUNT_OK.match(cell) else (None, cell)

    items, groups, current = [], [], []
    for r in rows[1:]:
        if not any(r):
            groups.append(current)
            current = []
            continue
        current.append(r)
    groups.append(current)

    for group in groups:
        pending, after_total = [], False
        for r in group:
            label = r[label_col]
            vals = {k: value(r[i]) for k, i in col_index.items()}
            row = {"line_item": label, "bureau": "", "kind": "line", **{k: v[0] for k, v in vals.items()},
                   **{f"{k}_printed": v[1] for k, v in vals.items()}, "note": ""}
            if label.startswith("Total "):
                bureau = label.removeprefix("Total ")
                if pending:
                    row.update(kind="bureau_total", bureau=bureau)
                    for p in pending:
                        p["bureau"] = bureau
                else:
                    row.update(kind="grand_total", bureau="All")
                pending, after_total = [], True
            elif after_total:
                row.update(kind="standalone", bureau=label)
            else:
                pending.append(row)
            items.append(row)
        for p in pending:  # a group without a total: each line stands alone
            p.update(kind="standalone", bureau=p["line_item"])

    notes = []
    grand = next(r for r in items if r["kind"] == "grand_total")
    for key in col_index:
        parts = [r for r in items if r["kind"] in ("bureau_total", "standalone")]
        unknown = [r for r in parts if r[key] is None]
        if len(unknown) == 1 and grand[key] is not None:
            implied = grand[key] - sum(r[key] for r in parts if r[key] is not None)
            u = unknown[0]
            u[key] = implied
            u["note"] = (f"{spec.columns[key]} printed as {u[f'{key}_printed']!r} (malformed in the source); "
                         f"implied by the column total: {implied:,}")
            notes.append(f"{u['line_item']}, {spec.columns[key]}: {u['note']}")
        if any(r[key] is None for r in parts):
            notes.append(f"{spec.columns[key]}: more than one unreadable value; column not reconciled")
            continue
        total = sum(r[key] for r in parts)
        status = "reconciles" if abs(total - grand[key]) <= 5 else f"DOES NOT reconcile ({total:,})"
        notes.append(f"{spec.columns[key]}: bureaus + standalone lines = {grand[key]:,} -> {status}")
        for t in (r for r in items if r["kind"] == "bureau_total"):
            members = [r for r in items if r["kind"] == "line" and r["bureau"] == t["bureau"]]
            s = sum(m[key] or 0 for m in members)
            if abs(s - t[key]) > 3:
                notes.append(f"{t['line_item']} {spec.columns[key]}: lines sum to {s:,}, printed {t[key]:,}")
    return items, notes


def exhibit_title(page: pymupdf.Page) -> str:
    first = clean_block(page.get_text("blocks")[2][4]) if len(page.get_text("blocks")) > 2 else ""
    for b in page.get_text("blocks"):
        t = " ".join(b[4].split())
        if t.startswith("Exhibit"):
            return t
    return first


# ------------------------------------------------------------------------------------- public research ledger

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_ISO = re.compile(r"\b(20\d\d-\d\d(?:-\d\d)?)\b")
_LEAD_YEAR = re.compile(r"^\*\*[^*]*?\b((?:19|20)\d\d)(?:\s*[–-]\s*(\d{2,4}))?\.?\*\*")
_CHECKED = re.compile(r"\(checked (\d{4}-\d\d-\d\d)\)")


def public_slug(section: str) -> str:
    """Chunk-id segment for a ledger section: its first three words."""
    return _slug(" ".join(section.split()[:3]))


def _plain(md: str) -> tuple[str, list[str]]:
    """Markdown -> plain text: link text kept (URLs returned separately), bold and \\$ escapes removed."""
    urls = [m.group(2) for m in _LINK.finditer(md)]
    text = _LINK.sub(r"\1", md).replace("**", "").replace("\\$", "$").replace("*", "")
    text = re.sub(r"(?<!\.)\.\.(?=\s|$)", ".", text)    # "announcement.**" + ". " -> one period, "..." kept
    return re.sub(r"\s+", " ", text).strip(), urls


def _date_cell(cell: str) -> str | None:
    """A table date cell: "2018-05", "2021-10-22", "2022–23" -> "2023", "2023-09 to 2024-08" -> "2024-08"."""
    isos = _ISO.findall(cell)
    if isos:
        return max(isos)
    m = re.fullmatch(r"\s*((?:19|20)\d\d)\s*(?:[–-]\s*(\d{2,4}))?\s*", cell)
    if m:
        end = m.group(2)
        return (m.group(1)[:4 - len(end)] + end) if end else m.group(1)
    return None


def date_public_item(raw: str, date_cell: str | None, section_checked: str | None,
                     ledger_checked: str, first_cell: str = "") -> tuple[str, str]:
    """(as_of, basis) for one ledger item; see the public_sources comment in the manifest for the order."""
    if date_cell and (d := _date_cell(date_cell)):
        return d, "date column"
    m = _LEAD_YEAR.match(raw.lstrip("-0123456789. "))
    if m:
        end = m.group(2)
        return ((m.group(1)[:4 - len(end)] + end) if end else m.group(1)), "event year in lead"
    if isos := _ISO.findall(raw):
        return max(isos), "latest cited publication date"
    if years := re.findall(r"\b((?:19|20)\d\d)\b", first_cell):    # "2021 financing, sites, team"
        return max(years), "year in first cell"
    if section_checked:
        return section_checked, "section checked date"
    years = re.findall(r"\b((?:19|20)\d\d)\b", raw)
    if years:
        return max(years), "latest year mentioned"
    return ledger_checked, "ledger checked date (current-state item)"


def parse_public(source, company_id: str, root: Path = ROOT) -> list[dict]:
    """One item per table row, list item or paragraph of each mapped section, dated and topic-tagged."""
    lines = (root / source.path).read_text().splitlines()
    items: list[dict] = []
    section, checked, header, counters = None, None, None, {}

    def add(raw: str, rendered: str, date_cell: str | None = None, first_cell: str | None = None):
        cfg = source.sections[section]
        text, urls = _plain(rendered)
        lead = _plain(first_cell)[0] if first_cell else text     # what manifest overrides match against
        as_of, basis = date_public_item(raw, date_cell, checked, source.checked, first_cell or "")
        for opening, d in source.item_dates.items():
            if lead.startswith(opening):
                as_of, basis = d, "manifest"
        topic = next((t for opening, t in source.item_topics.items() if lead.startswith(opening)), cfg.topic)
        slug = public_slug(section)
        n = counters[slug] = counters.get(slug, 0) + 1
        items.append({"id": f"{company_id}_{source.id}_{slug}_{n:02d}", "section": section, "lead": lead[:80], "topic": topic,
                      "reliability": cfg.reliability, "as_of": as_of, "date_basis": basis, "text": text, "urls": urls})

    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            checked = (m.group(1) if (m := _CHECKED.search(heading)) else None)
            heading = _CHECKED.sub("", heading).strip()
            section = heading if heading in source.sections else None
            header = None
            continue
        if section is None or not line.strip():
            header = None if not line.strip() else header
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not set("".join(cells)) <= set("-: "):
                is_date = re.search(r"date|screenshot field", header[0], re.I)
                rendered = ". ".join(f"{h}: {c.rstrip('. ')}" for h, c in zip(header, cells) if c)
                add(line, rendered, cells[0] if is_date else None, cells[0])
            continue
        header = None
        add(line, re.sub(r"^(?:-|\d+\.)\s+", "", line.strip()))

    missing = set(source.sections) - {i["section"] for i in items}
    if missing:
        raise ValueError(f"{source.path}: mapped sections not found: {sorted(missing)}")
    late = [i["text"][:60] for i in items if i["as_of"] > source.checked]
    if late:
        raise ValueError(f"{source.path}: items dated after the ledger was checked ({source.checked}): {late}")
    for opening in [*source.item_topics, *source.item_dates]:
        if sum(i["lead"].startswith(opening) for i in items) != 1:
            raise ValueError(f"{source.path}: override key {opening!r} must match exactly one item")
    return items


# --------------------------------------------------------------------------------------------- build (PDF)

def corpus_dir(manifest: CorpusManifest, root: Path = ROOT) -> Path:
    return root / manifest.corpus_dir


def build_corpus(manifest: CorpusManifest, root: Path = ROOT) -> dict:
    """Regenerate every derived file from the PDF. Curated files are never touched."""
    doc = pymupdf.open(root / manifest.case.path)
    out = corpus_dir(manifest, root)
    for sub in ("narrative", "exhibits", "sources"):
        (out / sub).mkdir(parents=True, exist_ok=True)
        if sub != "sources":
            for old in (out / sub).glob("*"):
                old.unlink()

    endnotes = parse_endnotes(doc, manifest)
    paras = read_paragraphs(doc, manifest, {n["number"] for n in endnotes})
    assign_topics(paras, manifest)
    chunks = narrative_chunks(paras, manifest)
    cited: dict[str, list[str]] = {}
    for c in chunks:
        for n in c.meta["notes"]:
            cited.setdefault(n, []).append(c.chunk_id)
    for n in endnotes:
        n["cited_by"] = cited.get(n["number"], [])
    (out / "sources" / "endnotes.yaml").write_text(_header_yaml("endnotes") + yaml.safe_dump(
        endnotes, sort_keys=False, allow_unicode=True, width=120))

    for i, (key, title) in enumerate(manifest.topics.items(), 1):
        mine = [c for c in chunks if c.topic == key]
        if not mine:
            continue
        lines = ["---", f"topic: {key}", f"title: {json.dumps(title, ensure_ascii=False)}",
                 f"source: {json.dumps(manifest.case.title, ensure_ascii=False)}", f"as_of: \"{manifest.case.as_of}\"",
                 "generated: true   # by `debate build-corpus`; edit configs/corpus/*.yaml and rebuild instead", "---",
                 "", f"# {title}", ""]
        for c in mine:
            meta = {"id": c.chunk_id, "pages": c.meta["pages"], "section": c.section, "as_of": c.as_of,
                    "notes": c.meta["notes"], "years_mentioned": c.meta["years_mentioned"]}
            lines += [f"<!-- chunk {json.dumps(meta, ensure_ascii=False)} -->", c.text, ""]
        (out / "narrative" / f"{i:02d}_{key}.md").write_text("\n".join(lines))

    reconciliation: list[str] = []
    for spec in manifest.exhibits:
        page = doc[spec.page - 1]
        rows = _cells(page)
        if spec.layout == "matrix":
            records = parse_matrix(rows, spec)
        else:
            records, notes = parse_budget(rows, spec)
            reconciliation += notes
        with (out / "exhibits" / f"{spec.id}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(records[0]))
            w.writeheader()
            w.writerows(records)
        (out / "exhibits" / f"{spec.id}.title.txt").write_text(exhibit_title(page) + "\n")

    captions = []
    if manifest.case.caption_pages:
        first, last = manifest.case.caption_pages
        from debate.rag.ingest import page_paragraphs
        for pno in range(first, last + 1):
            for c in chunk_exhibit_page(page_paragraphs(doc[pno - 1]), pno, "caption", manifest.company_id):
                captions.append({"id": c.chunk_id, "page": pno, "title": c.section, "text": c.text,
                                 "as_of": manifest.caption_as_of.get(pno, manifest.case.as_of)})
    (out / "captions.yaml").write_text(_header_yaml("figure captions") +
                                       yaml.safe_dump(captions, sort_keys=False, allow_unicode=True, width=120))

    public_items = 0
    for src in manifest.public_sources:
        parsed = parse_public(src, manifest.company_id, root)
        public_items += len(parsed)
        (out / "sources" / f"{src.id}.yaml").write_text(
            _header_yaml("public ledger items", src.path) +
            yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True, width=120))

    stats = {"paragraphs": len(paras), "narrative_chunks": len(chunks), "endnotes": len(endnotes),
             "public_items": public_items,
             "exhibits": len(manifest.exhibits), "captions": len(captions), "reconciliation": reconciliation}
    write_readme(manifest, root, stats)
    return stats


def _header_yaml(what: str, source: str = "the case PDF") -> str:
    return f"# GENERATED {what} by `debate build-corpus` from {source}; do not edit (rebuild instead).\n"


# ---------------------------------------------------------------------------------------- curated files

class Fact(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    topic: str
    statement: str
    kind: Literal["reported", "estimate", "claim", "forecast", "plan", "quote", "derived"]
    as_of: str = Field(pattern=DATE_PATTERN)
    page: int | None = None
    quote: str | None = None          # verbatim from the case; verified against the PDF at load time
    note: str | None = None           # endnote number the case cites for it
    attributed_to: str | None = None  # whose estimate / claim it is
    value: float | None = None
    unit: str | None = None
    formula: str | None = None        # derived facts: arithmetic re-checked in code
    derived_from: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "derived":
            if not self.formula or not self.derived_from:
                raise ValueError(f"derived fact {self.id} needs formula and derived_from")
        elif not (self.quote and self.page):
            raise ValueError(f"fact {self.id} needs a verbatim quote and its page")
        if self.kind in ("estimate", "claim") and not self.attributed_to:
            raise ValueError(f"{self.kind} {self.id} needs attributed_to")
        return self


class EvidenceRef(BaseModel):
    """Points at one public ledger item: the item in `section` whose text contains every `contains` string."""
    section: str
    contains: list[str] = Field(min_length=1)
    role: Literal["source", "cross_check"] = "source"   # source: the value comes from it; cross_check: tested against it


class Assumption(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    topic: str = "market"
    statement: str
    value: float
    unit: str
    as_of: str = Field(pattern=DATE_PATTERN)
    status: Literal["assumption", "external_unverified", "public_source"]
    basis: str
    source: str | None = None
    evidence: list[EvidenceRef] = []

    @model_validator(mode="after")
    def _sourced(self):
        if self.status == "public_source" and not any(e.role == "source" for e in self.evidence):
            raise ValueError(f"assumption {self.id}: status public_source needs an evidence entry with role source")
        return self


class SizingStep(BaseModel):
    ref: str
    op: Literal["*", "/"] = "*"


class SizingMethod(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    approach: Literal["top-down", "bottom-up"]
    as_of: str = Field(pattern=DATE_PATTERN)
    steps: list[SizingStep]
    unit: str = "USD per year"
    note: str | None = None   # caveat shown with the result


def _load_yaml(path: Path) -> list:
    if not path.exists():
        raise FileNotFoundError(f"curated file missing: {path}")
    return yaml.safe_load(path.read_text()) or []


def case_page_texts(doc: pymupdf.Document, known_notes: set[str]) -> dict[int, str]:
    """Normalized text per page, with endnote markers removed, for quote verification."""
    return {i + 1: normalize(extract_note_markers(" ".join(_raw_paragraphs(p)), known_notes)[0])
            for i, p in enumerate(doc)}


def load_facts(manifest: CorpusManifest, pages: dict[int, str], endnotes: dict[str, dict],
               root: Path = ROOT) -> list[Fact]:
    facts = [Fact(**f) for f in _load_yaml(root / manifest.curated.facts)]
    ids = [f.id for f in facts]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate fact ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    for f in facts:
        if f.topic not in manifest.topics:
            raise ValueError(f"fact {f.id}: unknown topic {f.topic!r}")
        if f.note and f.note not in endnotes:
            raise ValueError(f"fact {f.id}: endnote {f.note} does not exist")
        if f.quote:
            near = pages.get(f.page, "") + " " + pages.get(f.page + 1, "")  # a quote may cross a page break
            if normalize(f.quote) not in near:
                raise ValueError(f"fact {f.id}: quote not found on p.{f.page} of the case: {f.quote[:80]!r}")
        if f.kind == "derived":
            missing = [d for d in f.derived_from if d not in ids and not d.startswith("ex")]
            if missing:
                raise ValueError(f"fact {f.id}: derived_from refers to unknown ids {missing}")
            if check_calculation(f.formula) is not True:
                raise ValueError(f"fact {f.id}: formula does not check out: {f.formula}")
    return facts


def load_assumptions(manifest: CorpusManifest, root: Path = ROOT) -> list[Assumption]:
    items = [Assumption(**a) for a in _load_yaml(root / manifest.curated.assumptions)]
    if len({a.id for a in items}) != len(items):
        raise ValueError("duplicate assumption ids")
    return items


def load_public_items(manifest: CorpusManifest, root: Path = ROOT) -> list[dict]:
    """The parsed public ledgers written by build_corpus."""
    out = corpus_dir(manifest, root)
    items = []
    for src in manifest.public_sources:
        path = out / "sources" / f"{src.id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; run `python -m debate build-corpus`")
        items += yaml.safe_load(path.read_text()) or []
    return items


def resolve_evidence(assumptions: list[Assumption], public_items: list[dict]) -> dict[str, list[dict]]:
    """assumption id -> the ledger items it cites. Each reference must match exactly one item, and a
    public_source value must carry the date of the item it comes from."""
    resolved: dict[str, list[dict]] = {}
    for a in assumptions:
        for ref in a.evidence:
            wanted = [normalize(c).lower() for c in ref.contains]
            hits = [i for i in public_items if i["section"] == ref.section
                    and all(w in normalize(i["text"]).lower() for w in wanted)]
            if len(hits) != 1:
                raise ValueError(f"assumption {a.id}: evidence {ref.contains} matches {len(hits)} items in "
                                 f"{ref.section!r} (need exactly one)")
            item = hits[0]
            if ref.role == "source" and a.status == "public_source" and item["as_of"] != a.as_of:
                raise ValueError(f"assumption {a.id}: as_of {a.as_of} differs from its source {item['id']} "
                                 f"({item['as_of']})")
            resolved.setdefault(a.id, []).append({"id": item["id"], "as_of": item["as_of"], "role": ref.role})
    return resolved


def compute_sizing(manifest: CorpusManifest, facts: list[Fact], assumptions: list[Assumption],
                   root: Path = ROOT) -> list[dict]:
    """Recompute every sizing method from its inputs and record where each input comes from."""
    lookup = {f.id: ("fact" if f.kind != "derived" else "derived", f.kind, f.value, f.unit, f.statement)
              for f in facts if f.value is not None}
    lookup |= {a.id: ("public source" if a.status == "public_source" else "assumption", a.status, a.value, a.unit,
                      a.statement) for a in assumptions}
    results = []
    for m in (SizingMethod(**x) for x in _load_yaml(root / manifest.curated.sizing)):
        total, lineage = None, []
        for step in m.steps:
            if step.ref not in lookup:
                raise ValueError(f"sizing {m.id}: {step.ref} is not a fact or assumption with a value")
            source, kind, value, unit, statement = lookup[step.ref]
            v = Decimal(str(value))
            total = v if total is None else (total * v if step.op == "*" else total / v)
            lineage.append({"ref": step.ref, "op": step.op, "value": value, "unit": unit, "source": source,
                            "kind": kind, "statement": statement, "running": float(total)})
        results.append({"id": m.id, "name": m.name, "approach": m.approach, "as_of": m.as_of, "unit": m.unit,
                        "result": float(total), "lineage": lineage, "note": m.note})
    return results


# ------------------------------------------------------------------------------------------ chunk loading

_CHUNK_BLOCK = re.compile(r"<!-- chunk (\{.*?\}) -->\n(.*?)(?=\n<!-- chunk |\Z)", re.S)


def _money(v: float) -> str:
    for scale, suffix in ((1e9, "B"), (1e6, "M")):
        if abs(v) >= scale:
            return f"${v / scale:,.2f}{suffix}"
    return f"${v:,.0f}"


def _fmt(value: float, unit: str | None) -> str:
    if unit in ("USD", "$"):
        return _money(value)
    if unit == "share":
        return f"{value:.2%}"
    return f"{value:,.10g} {unit or ''}".strip()


def load_chunks(manifest: CorpusManifest, root: Path = ROOT) -> list[CorpusChunk]:
    """Everything that gets embedded, validated. Run build_corpus first."""
    out = corpus_dir(manifest, root)
    if not (out / "sources" / "endnotes.yaml").exists():
        raise FileNotFoundError(f"{out} has no generated corpus; run `python -m debate build-corpus` first")
    co = manifest.company_id
    endnotes = {n["number"]: n for n in yaml.safe_load((out / "sources" / "endnotes.yaml").read_text())}
    chunks: list[CorpusChunk] = []

    for path in sorted((out / "narrative").glob("*.md")):
        topic = re.search(r"^topic: (\S+)$", path.read_text(), re.M).group(1)
        for meta_json, text in _CHUNK_BLOCK.findall(path.read_text()):
            meta = json.loads(meta_json)
            chunks.append(CorpusChunk(meta["id"], "case", "narrative", topic, meta["as_of"], text.strip(),
                                      meta["pages"][0], meta["section"],
                                      {"pages": meta["pages"], "notes": meta["notes"]}))

    for spec in manifest.exhibits:
        title = (out / "exhibits" / f"{spec.id}.title.txt").read_text().strip()
        with (out / "exhibits" / f"{spec.id}.csv").open() as f:
            for i, row in enumerate(csv.DictReader(f), 1):
                if spec.layout == "matrix":
                    attrs = "; ".join(f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in row.items()
                                      if k not in ("group", "segment") and v)
                    text = f"{title}. {row['group']} - {row['segment']}. {attrs}."
                    cid = f"{co}_{spec.id}_{_slug(row['segment'])}"
                else:
                    amounts = "; ".join(f"{spec.columns[k]} {_money(float(row[k]))}" for k in spec.columns if row[k])
                    where = "" if row["kind"] in ("standalone", "grand_total") else f" ({row['bureau']})"
                    text = f"{title}. {row['line_item']}{where}: {amounts}." + (f" Note: {row['note']}" if row["note"] else "")
                    cid = f"{co}_{spec.id}_{_slug(row['line_item'])}"
                chunks.append(CorpusChunk(cid, spec.id, "exhibit_row", spec.topic, spec.as_of, text, spec.page,
                                          f"Exhibit {spec.number}", {"row": row}))

    for c in yaml.safe_load((out / "captions.yaml").read_text()) or []:
        chunks.append(CorpusChunk(c["id"], "caption", "exhibit_caption", "science_and_prior_art", c["as_of"],
                                  c["text"], c["page"], c["title"]))

    for n in endnotes.values():
        pages = sorted({int(p.split("_")[2][1:]) for p in n["cited_by"]})
        cited = f" Cited on p.{', p.'.join(map(str, pages))} of the case." if pages else ""
        chunks.append(CorpusChunk(f"{co}_note_{n['number']}", "note", "endnote", "sources",
                                  n["published"] or manifest.case.published,
                                  f"Endnote {n['number']} (published {n['published']}): {n['citation']}{cited}",
                                  n["page"], "Endnotes", {"url": n["url"], "date_basis": n["date_basis"]}))

    doc = pymupdf.open(root / manifest.case.path)
    facts = load_facts(manifest, case_page_texts(doc, set(endnotes)), endnotes, root)
    for f in facts:
        src = ""
        if f.note:
            n = endnotes[f.note]
            src = f" Case source: endnote {f.note} (published {n['published']})."
        who = f", per {f.attributed_to}" if f.attributed_to else ""
        value = f" Value: {_fmt(f.value, f.unit)}." if f.value is not None else ""
        how = f" Calculation: {f.formula}." if f.formula else ""
        quote = f" Case p.{f.page}: “{f.quote}”" if f.quote else ""
        chunks.append(CorpusChunk(f"{co}_fact_{f.id}", "fact", "fact", f.topic, f.as_of,
                                  f"[{f.kind}{who}; as of {f.as_of}] {f.statement}{value}{how}{src}{quote}",
                                  f.page, None, {"kind": f.kind, "value": f.value, "unit": f.unit}))

    public = load_public_items(manifest, root)
    for i in public:
        lead = i["reliability"] == "lead"
        label = (f"UNVERIFIED LEAD (screenshot transcription, not a primary source; {i['section']}; as of {i['as_of']})"
                 if lead else f"[public source: {i['section']}; as of {i['as_of']}]")
        chunks.append(CorpusChunk(i["id"], "public", "public_lead" if lead else "public_source", i["topic"],
                                  i["as_of"], f"{label} {i['text']}", None, i["section"],
                                  {"urls": i["urls"], "date_basis": i["date_basis"], "reliability": i["reliability"]}))

    assumptions = load_assumptions(manifest, root)
    cites = resolve_evidence(assumptions, public)
    for a in assumptions:
        refs = cites.get(a.id, [])
        label = ("SIZING INPUT from public sources - not stated in the case" if a.status == "public_source"
                 else f"ASSUMPTION - not stated in the case ({a.status})")
        evidence = "".join(f" {'Source' if r['role'] == 'source' else 'Cross-check'}: {r['id']} (as of {r['as_of']})."
                           for r in refs)
        chunks.append(CorpusChunk(
            f"{co}_assume_{a.id}", "assume", "assumption", a.topic, a.as_of,
            f"{label}: {a.statement} Value: {_fmt(a.value, a.unit)}. Basis: {a.basis}" +
            (f" Source: {a.source}." if a.source else "") + evidence, None, None,
            {"status": a.status, "evidence": refs}))

    for s in compute_sizing(manifest, facts, assumptions, root):
        steps = " -> ".join(f"{'' if i == 0 else ('x ' if l['op'] == '*' else '/ ')}{_fmt(l['value'], l['unit'])} "
                            f"[{l['source']}: {l['ref']}]" for i, l in enumerate(s["lineage"]))
        chunks.append(CorpusChunk(f"{co}_sizing_{s['id']}", "sizing", "sizing_model", "market", s["as_of"],
                                  f"Sizing method '{s['name']}' ({s['approach']}): {steps} = {_money(s['result'])} "
                                  f"{s['unit']}." + (f" Caveat: {s['note']}" if s["note"] else ""), None, None,
                                  {"result": s["result"]}))

    validate_chunks(chunks, manifest)
    return chunks


def validate_chunks(chunks: list[CorpusChunk], manifest: CorpusManifest) -> None:
    ids = [c.chunk_id for c in chunks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate chunk ids: {dupes[:10]}")
    for c in chunks:
        if not _DATE.match(c.as_of or ""):
            raise ValueError(f"chunk {c.chunk_id} has no valid as_of date ({c.as_of!r})")
        if c.topic not in manifest.topics and c.topic != "sources":
            raise ValueError(f"chunk {c.chunk_id} has unknown topic {c.topic!r}")
        if not c.text.strip():
            raise ValueError(f"chunk {c.chunk_id} is empty")


# ---------------------------------------------------------------------------------------------- README

def write_readme(manifest: CorpusManifest, root: Path, stats: dict) -> None:
    out = corpus_dir(manifest, root)
    lines = [f"# {manifest.company_id.title()} corpus", "",
             f"Generated by `debate build-corpus` from {manifest.case.title}. Every chunk is dated (`as_of`).",
             "Generated folders are rebuilt from the PDF; `facts.yaml`, `assumptions.yaml` and `sizing.yaml` are "
             "curated by hand.", "", "## Layout", "",
             "| Path | What | Kind |", "|---|---|---|"]
    for p in sorted((out / "narrative").glob("*.md")):
        n = p.read_text().count("<!-- chunk ")
        lines.append(f"| `narrative/{p.name}` | {n} paragraphs | generated |")
    lines += [f"| `exhibits/{e.id}.csv` | Exhibit {e.number} as rows | generated |" for e in manifest.exhibits]
    lines += [f"| `captions.yaml` | {stats['captions']} figure captions | generated |",
              f"| `sources/endnotes.yaml` | {stats['endnotes']} endnotes, each with its publication date | generated |",
              *[f"| `sources/{p.id}.yaml` | {p.title}: one dated item per table row or bullet of `{p.path}` | generated |"
                for p in manifest.public_sources],
              "| `facts.yaml` | atomic facts, quotes verified against the PDF | curated |",
              "| `assumptions.yaml` | sizing inputs that are NOT in the case | curated |",
              "| `sizing.yaml` | TAM methods recomputed from facts and assumptions | curated |",
              "", "## Exhibit 7 reconciliation", ""] + [f"- {n}" for n in stats["reconciliation"]]
    try:
        doc = pymupdf.open(root / manifest.case.path)
        endnotes = {n["number"]: n for n in yaml.safe_load((out / "sources" / "endnotes.yaml").read_text())}
        facts = load_facts(manifest, case_page_texts(doc, set(endnotes)), endnotes, root)
        sizing = compute_sizing(manifest, facts, load_assumptions(manifest, root), root)
        lines += ["", f"## Curated facts: {len(facts)}", ""]
        kinds = {}
        for f in facts:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        lines += [f"- {k}: {n}" for k, n in sorted(kinds.items())]
        public = load_public_items(manifest, root)
        if public:
            lines += ["", f"## Public sources: {len(public)} items", "",
                      "| Section | Items | Dated | Reliability |", "|---|---:|---|---|"]
            for sec in dict.fromkeys(i["section"] for i in public):
                mine = [i for i in public if i["section"] == sec]
                dates = sorted(i["as_of"] for i in mine)
                span = dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"
                lines.append(f"| {sec} | {len(mine)} | {span} | {mine[0]['reliability']} |")
            bases = {}
            for i in public:
                bases[i["date_basis"]] = bases.get(i["date_basis"], 0) + 1
            lines += ["", "How each item was dated: " + "; ".join(f"{b} {n}" for b, n in bases.items()) + "."]
            cites = resolve_evidence(load_assumptions(manifest, root), public)
            if cites:
                lines += ["", "Sizing inputs backed by public items: " + "; ".join(
                    f"`{a}` <- " + ", ".join(f"`{r['id']}` ({r['role']})" for r in refs) for a, refs in cites.items())
                    + "."]
        lines += ["", "## Sizing lineage", ""]
        for s in sizing:
            lines += [f"### {s['name']} ({s['approach']}) = {_money(s['result'])}", ""]
            if s["note"]:
                lines += [f"> {s['note']}", ""]
            lines += ["| Step | Input | Value | Source |", "|---|---|---:|---|"]
            for i, l in enumerate(s["lineage"]):
                op = "" if i == 0 else ("×" if l["op"] == "*" else "÷")
                lines.append(f"| {op} | `{l['ref']}` {l['statement'][:70]} | {_fmt(l['value'], l['unit'])} | "
                             f"{l['source']} ({l['kind']}) |")
            lines.append("")
    except FileNotFoundError as e:
        lines += ["", f"_Curated files not loaded: {e}_"]
    (out / "README.md").write_text("\n".join(lines) + "\n")
