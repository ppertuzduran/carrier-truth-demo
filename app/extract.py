"""Deterministic extraction with provenance.

Every extracted value keeps the file hash, page, bounding box and raw line it
came from. Every candidate is kept (a document can contain more than one
premium), so ambiguity is visible instead of silently resolved.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .models import DocType

EXTRACTOR_VERSION = "label-anchor-v1"

MONEY = r"\$(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
FREQ = r"(?P<freq>MONTHLY|ANNUAL|ANNUALLY|QUARTERLY|SEMI-ANNUAL|SEMIANNUAL)"

PATTERNS = {
    "client_name": re.compile(r"^(?:Applicant|Insured|Proposed Insured)\s*:\s*(?P<value>.+?)\s*$", re.I),
    "carrier": re.compile(r"^Carrier\s*:\s*(?P<value>.+?)\s*$", re.I),
    "coverage": re.compile(r"^(?:Face Amount|Death Benefit|Coverage)\s*:\s*" + MONEY + r"\s*$", re.I),
    "premium": re.compile(r"^(?P<label>[A-Za-z ]*Premium[A-Za-z ]*?)\s*:\s*" + MONEY + r"\s+" + FREQ + r"\s*$", re.I),
}

# Any of these anywhere on the document removes its authority (negative evidence beats the title).
NON_AUTHORITATIVE_MARKERS = [
    re.compile(r"\bnot (an? )?(issued|official|final)\b", re.I),
    re.compile(r"\bportal (print )?(view|preview)\b", re.I),
    re.compile(r"\b(preview|draft|sample|specimen|unofficial|estimate[sd]?)\b", re.I),
]
AUTHORITATIVE_MARKERS = [re.compile(r"^Carrier Illustration$", re.I)]


@dataclass
class Candidate:
    field: str
    value: str
    raw_text: str
    page: int
    bbox: list[float]
    amount: Decimal | None = None
    freq: str | None = None
    label: str | None = None


@dataclass
class ExtractedDoc:
    file: str
    sha256: str
    doc_type: DocType
    doc_type_reason: str
    doc_type_line: dict | None = None
    lines: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)   # [{"page", "width", "height"}] in PDF points
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(lines: list[dict]) -> tuple[DocType, str, dict | None]:
    # Negative evidence wins: a disclaimer anywhere overrides any title.
    for ln in lines:
        for m in NON_AUTHORITATIVE_MARKERS:
            if m.search(ln["text"]):
                return DocType.PORTAL_PREVIEW, f'Non-authoritative marker found: "{ln["text"]}"', ln
    for ln in lines[:3]:
        for m in AUTHORITATIVE_MARKERS:
            if m.search(ln["text"]):
                return DocType.ISSUED_ILLUSTRATION, f'Title "{ln["text"]}" identifies an issued carrier illustration', ln
    return DocType.UNKNOWN, "No known document template matched; would route to LLM-assisted review", None


def extract(path: Path) -> ExtractedDoc:
    lines: list[dict] = []
    pages: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            pages.append({"page": page_no, "width": float(page.width), "height": float(page.height)})
            for ln in page.extract_text_lines(return_chars=False):
                lines.append({
                    "page": page_no,
                    "text": ln["text"].strip(),
                    "bbox": [round(ln["x0"], 2), round(ln["top"], 2), round(ln["x1"], 2), round(ln["bottom"], 2)],
                })

    doc_type, reason, type_line = classify(lines)
    cands: dict[str, list[Candidate]] = {k: [] for k in PATTERNS}
    for ln in lines:
        for fname, rx in PATTERNS.items():
            m = rx.match(ln["text"])
            if not m:
                continue
            c = Candidate(field=fname, value="", raw_text=ln["text"], page=ln["page"], bbox=ln["bbox"])
            if fname in ("coverage", "premium"):
                c.amount = Decimal(m.group("amount").replace(",", ""))
                c.value = f"${c.amount:,.2f}"
                if fname == "premium":
                    c.freq = m.group("freq").upper().replace("ANNUALLY", "ANNUAL").replace("SEMIANNUAL", "SEMI-ANNUAL")
                    c.label = m.group("label").strip()
                    c.value += f" {c.freq}"
            else:
                c.value = m.group("value")
            cands[fname].append(c)

    return ExtractedDoc(file=path.name, sha256=sha256_of(path), doc_type=doc_type,
                        doc_type_reason=reason, doc_type_line=type_line, lines=lines, pages=pages,
                        candidates=cands)
