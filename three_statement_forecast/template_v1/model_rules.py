"""Shared rules used by both the builder and the linter, so they can never disagree."""
import re
from pathlib import Path

import yaml

SPEC = yaml.safe_load((Path(__file__).parent / "style_spec.yaml").read_text())

_SHEET_REF = r"(?:'[^']+'|[A-Za-z_][\w\.]*)!\$?[A-Z]{1,3}\$?\d+"
_NAME_REF = r"[A-Za-z_][A-Za-z0-9_]*"
_PURE_LINK = re.compile(rf"=(?:{_SHEET_REF}|(?!\$?[A-Z]{{1,3}}\$?\d+$){_NAME_REF})")


def cell_kind(value):
    """input | link | formula | None, the basis for font colour."""
    if isinstance(value, str) and value.startswith("="):
        return "link" if _PURE_LINK.fullmatch(value) else "formula"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "input"
    return None


def font_color(kind):
    c = SPEC["colors"]
    return {"input": c["input_font"], "link": c["link_font"]}.get(kind, c["formula_font"])


_STRINGS = re.compile(r'"[^"]*"|\'[^\']*\'')
_LITERAL = re.compile(r"(?<![A-Za-z_$\d.])\d+(?:\.\d+)?(?![A-Za-z_\d(])")


def formula_literals(formula):
    """Numeric literals embedded in a formula (cell refs and named ranges excluded)."""
    body = _STRINGS.sub("", formula[1:])
    return [float(x) for x in _LITERAL.findall(body)]
