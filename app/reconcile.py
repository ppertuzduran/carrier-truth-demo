"""Reconciliation rules (authority-based, fail-closed), run as a layered pipeline.

L1 source authority → L2 extraction → L3 semantic roles → L4 normalization →
L5 cross-source reconciliation → L6 plausibility → L7 LLM adjudication (routed only when
the deterministic layers meet something they don't recognize).

The source of truth for money and coverage is the carrier document. The requested
value and the UI value are *claims* checked against it, never used to fill or
override it. Majority voting is intentionally not used: two sources agreeing
against the carrier document (C001) must still block.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

from .annotate import annotate
from .extract import EXTRACTOR_VERSION, Candidate, ExtractedDoc, extract
from .models import (VERIFIED_STATUSES, CaseResult, CaseStatus, DocType, Evidence,
                     FieldResult, FieldStatus, LayerCheck)

RULES_VERSION = "rules-2026-09-30.2"

MONEY_TOLERANCE = Decimal("0.01")   # rounding only; any larger money gap is material
NAME_MIN_SIMILARITY = 0.92
PLACEHOLDER_NAMES = {"client", "applicant", "insured", "proposed insured", "customer", "valued customer",
                     "name", "insured name", "client name", "n/a", "na", "tbd", "test", "john doe", "jane doe"}
PAYMENTS_PER_YEAR = {"MONTHLY": 12, "QUARTERLY": 4, "SEMI-ANNUAL": 2, "ANNUAL": 1}

# Domain bounds (plausibility). Deliberately conservative; tune per carrier/product.
MIN_FACE_AMOUNT = Decimal("10000")
MAX_ANNUAL_PREMIUM_TO_FACE = Decimal("0.10")

# Semantic roles for premium labels. Unknown labels are never guessed: they are routed to adjudication.
BASE_PREMIUM_LABELS = {"premium", "base premium", "planned premium", "modal premium", "policy premium"}
OPTIONAL_PREMIUM_RX = re.compile(r"\b(rider|optional|option|alternative|with)\b", re.I)


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.casefold().split())


def _money(d: Decimal | float | int | None) -> str | None:
    return None if d is None else f"${Decimal(str(d)):,.2f}"


def premium_role(label: str | None) -> str:
    norm = " ".join((label or "").casefold().split())
    if norm in BASE_PREMIUM_LABELS:
        return "base"
    if OPTIONAL_PREMIUM_RX.search(norm):
        return "optional_rider"
    return "unknown"


def _doc_evidence(c: Candidate, doc: ExtractedDoc, role: str | None = None) -> Evidence:
    return Evidence(source="document", locator=f"{doc.file}#page={c.page}", raw_text=c.raw_text,
                    page=c.page, bbox=c.bbox, doc_sha256=doc.sha256, method=EXTRACTOR_VERSION, role=role)


def _req_evidence(case_id: str, key: str, value) -> Evidence:
    return Evidence(source="quote_request", locator=f"quote_requests.json[{case_id}].{key}",
                    raw_text=str(value), method="json-field")


def _single(field: str, doc: ExtractedDoc) -> tuple[Candidate | None, FieldResult | None]:
    """Return the only candidate, or a terminal FieldResult when missing/ambiguous."""
    cands = doc.candidates.get(field, [])
    if not cands:
        return None, FieldResult(field=field, status=FieldStatus.MISSING,
                                 reason=f"No '{field}' found in {doc.file}.")
    distinct = {c.value for c in cands}
    if len(distinct) > 1:
        return None, FieldResult(
            field=field, status=FieldStatus.AMBIGUOUS,
            document_value=" | ".join(sorted(distinct)),
            reason=f"The carrier document shows {len(distinct)} different values for {field}; picking one would be a guess.",
            evidence=[_doc_evidence(c, doc) for c in cands])
    return cands[0], None


def check_name(case: dict, doc: ExtractedDoc) -> FieldResult:
    c, terminal = _single("client_name", doc)
    req = case.get("requested_name")
    req_ev = [_req_evidence(case["case_id"], "requested_name", req)]
    if terminal:
        terminal.requested_value = req
        terminal.evidence += req_ev
        return terminal
    ev = [_doc_evidence(c, doc)] + req_ev
    if _norm_name(c.value) in PLACEHOLDER_NAMES:
        return FieldResult(field="client_name", status=FieldStatus.PLACEHOLDER, document_value=c.value,
                           requested_value=req, evidence=ev,
                           reason=f'Document names the applicant "{c.value}", a placeholder. Identity cannot be tied to this document.')
    sim = difflib.SequenceMatcher(None, _norm_name(c.value), _norm_name(req or "")).ratio()
    if sim >= NAME_MIN_SIMILARITY:
        return FieldResult(field="client_name", status=FieldStatus.VERIFIED, verified_value=c.value,
                           document_value=c.value, requested_value=req, evidence=ev,
                           reason=f"Document applicant matches the requested client (similarity {sim:.2f}).")
    return FieldResult(field="client_name", status=FieldStatus.CONTRADICTED, document_value=c.value,
                       requested_value=req, evidence=ev,
                       reason=f'Document applicant "{c.value}" does not match requested "{req}" (similarity {sim:.2f}).')


def check_carrier(case: dict, doc: ExtractedDoc) -> FieldResult:
    c, terminal = _single("carrier", doc)
    if terminal:
        return terminal
    return FieldResult(field="carrier", status=FieldStatus.VERIFIED, verified_value=c.value, document_value=c.value,
                       evidence=[_doc_evidence(c, doc)],
                       reason="Taken from the carrier document (the quote request has no carrier to cross-check).")


def check_coverage(case: dict, doc: ExtractedDoc) -> FieldResult:
    c, terminal = _single("coverage", doc)
    req = case.get("requested_coverage")
    req_s = _money(req)
    req_ev = [_req_evidence(case["case_id"], "requested_coverage", req)]
    if terminal:
        terminal.requested_value = req_s
        terminal.evidence += req_ev
        return terminal
    ev = [_doc_evidence(c, doc)] + req_ev
    if req is not None and abs(c.amount - Decimal(str(req))) <= MONEY_TOLERANCE:
        return FieldResult(field="coverage", status=FieldStatus.VERIFIED, verified_value=c.value,
                           document_value=c.value, requested_value=req_s, evidence=ev,
                           reason="Carrier document face amount matches the requested coverage.")
    return FieldResult(field="coverage", status=FieldStatus.CONTRADICTED, document_value=c.value,
                       requested_value=req_s, evidence=ev,
                       reason=f"Carrier document says {c.value} but {req_s} was requested. Coverage is a material field: refusing to verify.")


def _matches(c: Candidate, ui: Decimal) -> str | None:
    """'exact' if same monthly amount, 'derived' if equal only after converting the payment mode."""
    if c.freq == "MONTHLY" and abs(c.amount - ui) <= MONEY_TOLERANCE:
        return "exact"
    monthly = (c.amount * PAYMENTS_PER_YEAR.get(c.freq, 12) / 12).quantize(Decimal("0.01"))
    if c.freq != "MONTHLY" and abs(monthly - ui) <= MONEY_TOLERANCE:
        return "derived"
    return None


def check_premium(case: dict, doc: ExtractedDoc) -> FieldResult:
    cands = doc.candidates.get("premium", [])
    ui = case.get("ui_premium")
    ui_s = _money(ui)
    ui_ev = [_req_evidence(case["case_id"], "ui_premium", ui)] if ui is not None else []
    if not cands:
        return FieldResult(field="premium", status=FieldStatus.MISSING, ui_value=ui_s, evidence=ui_ev,
                           reason=f"No premium found in {doc.file}.")

    # L3: give every premium line a semantic role before comparing anything.
    roles = [(c, premium_role(c.label)) for c in cands]
    ev_all = [_doc_evidence(c, doc, role) for c, role in roles] + ui_ev
    base = [c for c, r in roles if r == "base"]
    optional = [c for c, r in roles if r == "optional_rider"]
    unknown = [c for c, r in roles if r == "unknown"]
    options_text = " | ".join(dict.fromkeys(c.value for c in cands))

    if unknown:
        return FieldResult(field="premium", status=FieldStatus.AMBIGUOUS, document_value=options_text, ui_value=ui_s,
                           evidence=ev_all,
                           reason=("Unrecognized premium label(s): " + ", ".join(f'"{c.label}"' for c in unknown) +
                                   ". Routed to adjudication instead of guessing its meaning."))
    distinct_base = {(c.amount, c.freq) for c in base}
    if len(distinct_base) > 1:
        return FieldResult(field="premium", status=FieldStatus.AMBIGUOUS, document_value=options_text, ui_value=ui_s,
                           evidence=ev_all, reason="The carrier document states different base premiums.")
    if not base:
        return FieldResult(field="premium", status=FieldStatus.AMBIGUOUS, document_value=options_text, ui_value=ui_s,
                           evidence=ev_all, reason="Only optional (rider) premiums were found; the base premium is missing.")

    b = base[0]
    excluded = ""
    if optional:
        excluded = "optional rider " + ", ".join(dict.fromkeys(c.value for c in optional)) + " excluded"
    detail = f"base premium · {excluded}" if excluded else None
    rider_note = (f" The document also offers {', '.join(dict.fromkeys(c.value for c in optional))} with an optional "
                  "rider; that is an add-on, not the policy premium, and the quote does not elect it.") if optional else ""

    if ui is None:
        return FieldResult(field="premium", status=FieldStatus.VERIFIED, verified_value=b.value, document_value=b.value,
                           detail=detail, evidence=ev_all, reason="Base premium taken from the carrier document." + rider_note)
    ui_d = Decimal(str(ui))
    match = _matches(b, ui_d)
    if match == "exact":
        return FieldResult(field="premium", status=FieldStatus.VERIFIED, verified_value=b.value, document_value=b.value,
                           ui_value=ui_s, detail=detail, evidence=ev_all,
                           reason="Carrier document base premium matches the UI premium." + rider_note)
    if match == "derived":
        monthly = (b.amount * PAYMENTS_PER_YEAR[b.freq] / 12).quantize(Decimal("0.01"))
        return FieldResult(field="premium", status=FieldStatus.VERIFIED_NORMALIZED, verified_value=b.value,
                           document_value=b.value, ui_value=ui_s,
                           derived_value=f"{_money(monthly)}/mo (derived: {b.value} ÷ {12 // PAYMENTS_PER_YEAR[b.freq]})",
                           detail=f"carrier quotes {b.freq.lower()}", evidence=ev_all,
                           reason=(f"Carrier quotes {b.value}; the UI shows {ui_s}, which equals it only as a derived monthly "
                                   f"average. The carrier figure is presented; the monthly value is labeled as derived "
                                   "(a real monthly mode must be re-quoted: modal factors usually make it cost more)."))
    if any(_matches(o, ui_d) for o in optional):
        return FieldResult(field="premium", status=FieldStatus.AMBIGUOUS, document_value=options_text, ui_value=ui_s,
                           evidence=ev_all,
                           reason=("The UI shows the rider-inclusive premium, but the quote does not record a rider election. "
                                   "Confirm the rider before presenting it."))
    return FieldResult(field="premium", status=FieldStatus.CONTRADICTED, document_value=b.value, ui_value=ui_s,
                       detail=detail if optional else None, evidence=ev_all,
                       reason=f"Carrier document base premium is {b.value} but the UI shows {ui_s}. Money mismatch: refusing to verify.")


CHECKS = [check_name, check_carrier, check_coverage, check_premium]
MATERIAL = {"coverage", "premium"}


def _plausibility(doc: ExtractedDoc, fields: list[FieldResult]) -> tuple[list[FieldResult], list[str]]:
    """L6: domain bounds. Catches a wrong number even when every source agrees on it."""
    problems: list[str] = []
    cov = doc.candidates.get("coverage", [])
    prem = [c for c in doc.candidates.get("premium", []) if premium_role(c.label) == "base"]
    face = cov[0].amount if len(cov) == 1 else None
    if face is not None and face < MIN_FACE_AMOUNT:
        problems.append(f"face amount {_money(face)} is below the {_money(MIN_FACE_AMOUNT)} minimum for a life policy")
    if face and prem:
        annual = prem[0].amount * PAYMENTS_PER_YEAR.get(prem[0].freq, 12)
        ratio = annual / face
        if ratio > MAX_ANNUAL_PREMIUM_TO_FACE:
            problems.append(f"annual premium {_money(annual)} is {ratio:.0%} of the face amount (max "
                            f"{MAX_ANNUAL_PREMIUM_TO_FACE:.0%})")
    if not problems:
        return fields, problems
    out = []
    for f in fields:
        if f.field == "coverage" and f.status in VERIFIED_STATUSES:
            data = f.model_dump()
            data.update(status=FieldStatus.IMPLAUSIBLE, verified_value=None,
                        reason="All sources agree, but the value fails domain checks: " + "; ".join(problems) + ".")
            out.append(FieldResult(**data))
        else:
            out.append(f)
    return out, problems


def _next_action(fields: list[FieldResult], doc: ExtractedDoc) -> str:
    if doc.doc_type != DocType.ISSUED_ILLUSTRATION:
        return ("Do not present these numbers. This file is not an issued carrier illustration "
                f"({doc.doc_type.value}). Run/download the official illustration from the carrier and re-verify.")
    msgs = []
    for f in fields:
        if f.status == FieldStatus.CONTRADICTED and f.field in MATERIAL:
            msgs.append(f"Re-run the illustration: {f.field} on the carrier document ({f.document_value}) "
                        f"differs from the quote ({f.requested_value or f.ui_value}).")
        elif f.status == FieldStatus.CONTRADICTED:
            msgs.append(f"Confirm {f.field}: document says {f.document_value}, quote says {f.requested_value}.")
        elif f.status == FieldStatus.IMPLAUSIBLE:
            msgs.append(f"Do not present {f.field}: {f.reason}")
        elif f.status == FieldStatus.AMBIGUOUS:
            msgs.append(f"Ask the agent to confirm which {f.field} option applies ({f.document_value}).")
        elif f.status == FieldStatus.PLACEHOLDER:
            msgs.append(f"Regenerate the illustration with the client's legal name (document shows \"{f.document_value}\").")
        elif f.status == FieldStatus.MISSING:
            msgs.append(f"{f.field} not found on the document: manual review.")
    return " ".join(msgs) if msgs else "Safe to present to the client and to use for the application."


def _layers(doc: ExtractedDoc, raw: list[FieldResult], final: list[FieldResult], plaus: list[str]) -> list[LayerCheck]:
    """Summarize each pipeline layer and the tool behind it (shown in the UI and the API)."""
    by = {f.field: f for f in raw}
    authoritative = doc.doc_type == DocType.ISSUED_ILLUSTRATION
    L: list[LayerCheck] = []

    L.append(LayerCheck(layer="L1", name="Source authority",
                        tool="Deterministic document classifier: required positive markers; a disclaimer beats any title",
                        status="pass" if authoritative else "fail", detail=doc.doc_type_reason))

    missing = [n for n in ("client_name", "carrier", "coverage", "premium") if not doc.candidates.get(n)]
    L.append(LayerCheck(layer="L2", name="Extraction + provenance",
                        tool="pdfplumber text lines · label-anchored regex · every candidate kept with page, bbox, sha256",
                        status="fail" if missing else "pass",
                        detail=(f"Missing: {', '.join(missing)}" if missing else
                                f"4/4 fields located · {sum(len(v) for v in doc.candidates.values())} candidate lines")))

    p = by.get("premium")
    roles = [premium_role(c.label) for c in doc.candidates.get("premium", [])]
    if "unknown" in roles:
        l3 = ("warn", "Unrecognized premium label: not guessed, routed to L7")
    elif "optional_rider" in roles:
        l3 = ("pass", f"Premium roles resolved: {p.detail}" if p and p.detail else "Optional rider premium separated from base")
    else:
        l3 = ("pass", "One premium line, role: base")
    L.append(LayerCheck(layer="L3", name="Semantic roles",
                        tool="Label → role taxonomy (base / optional rider / unknown); unknown is never guessed",
                        status=l3[0], detail=l3[1]))

    derived = [f for f in raw if f.derived_value]
    L.append(LayerCheck(layer="L4", name="Normalization",
                        tool="Decimal money · payment-mode conversion (labeled as derived) · accent/case-insensitive names",
                        status="warn" if derived else "pass",
                        detail=("; ".join(f"{f.field}: {f.derived_value}" for f in derived) if derived
                                else "No conversion needed")))

    bad = [f.field for f in raw if f.status == FieldStatus.CONTRADICTED]
    soft = [f"{f.field} ({f.status.value.lower()})" for f in raw if f.status in (FieldStatus.PLACEHOLDER, FieldStatus.AMBIGUOUS)]
    L.append(LayerCheck(layer="L5", name="Cross-source reconciliation",
                        tool="Authority order doc > request > UI · $0.01 money tolerance · name similarity ≥ 0.92 · placeholder list",
                        status="fail" if bad else ("warn" if soft else "pass"),
                        detail=("Contradicted: " + ", ".join(bad) if bad else
                                ("Unresolved: " + ", ".join(soft) if soft else "All claims agree with the carrier document"))))

    L.append(LayerCheck(layer="L6", name="Plausibility",
                        tool=f"Domain bounds: face ≥ {_money(MIN_FACE_AMOUNT)} · annual premium ≤ {MAX_ANNUAL_PREMIUM_TO_FACE:.0%} of face",
                        status="fail" if plaus else "pass",
                        detail="; ".join(plaus) if plaus else "Within bounds"))

    if not authoritative:
        # Consistency checks still ran, but on a source with no authority they can't verify anything.
        for chk in L[2:]:
            chk.status, chk.detail = "skip", f"Informational only (source not authoritative): {chk.detail}"
        l7 = ("skip", "Not applicable: an LLM cannot grant authority to a non-authoritative document")
    elif "unknown" in roles or missing:
        l7 = ("warn", "Would route: unrecognized content. Claude returns value + verbatim quote; code re-checks it (not run in demo)")
    else:
        l7 = ("skip", "Not needed: every label and role was recognized deterministically (zero LLM cost)")
    L.append(LayerCheck(layer="L7", name="LLM adjudication",
                        tool="Claude structured output + verbatim-quote grounding check; only for what L2–L3 can't resolve",
                        status=l7[0], detail=l7[1]))
    return L


def verify_case(case: dict, pdf_path: Path | None) -> CaseResult:
    if pdf_path is None or not pdf_path.exists():
        fields = [FieldResult(field=n, status=FieldStatus.MISSING, reason="No carrier document for this case.")
                  for n in ("client_name", "carrier", "coverage", "premium")]
        return CaseResult(case_id=case["case_id"], status=CaseStatus.BLOCKED, document_type=DocType.UNKNOWN,
                          document_file=None, document_sha256=None, fields=fields,
                          next_action="Attach the carrier illustration.", rules_version=RULES_VERSION,
                          extractor_version=EXTRACTOR_VERSION, request=case)

    doc = extract(pdf_path)
    raw = [chk(case, doc) for chk in CHECKS]
    fields, plaus = _plausibility(doc, raw)

    if doc.doc_type != DocType.ISSUED_ILLUSTRATION:
        # Values may be readable and may even agree, but the source has no authority: downgrade everything.
        downgraded = []
        marker = doc.doc_type_line
        marker_ev = [Evidence(source="document", locator=f"{doc.file}#page={marker['page']}", raw_text=marker["text"],
                              page=marker["page"], bbox=marker["bbox"], doc_sha256=doc.sha256,
                              method="doc-classifier-v1")] if marker else []
        for f in fields:
            data = f.model_dump()
            data.update(status=FieldStatus.UNVERIFIABLE_SOURCE, verified_value=None, derived_value=None, detail=None,
                        evidence=[e.model_dump() for e in f.evidence] + [e.model_dump() for e in marker_ev],
                        reason=f"{doc.doc_type_reason}. Reads {f.document_value or 'nothing'}, but only an issued "
                               "carrier document can verify it. (Would have been: " + f.status.value + ")")
            downgraded.append(FieldResult(**data))
        fields = downgraded

    fields = [f.model_copy(update={"notes": annotate(f)}) for f in fields]

    statuses = {f.status for f in fields}
    if all(s in VERIFIED_STATUSES for s in statuses):
        status = CaseStatus.VERIFIED
    elif statuses & {FieldStatus.CONTRADICTED, FieldStatus.UNVERIFIABLE_SOURCE, FieldStatus.MISSING,
                     FieldStatus.IMPLAUSIBLE}:
        status = CaseStatus.BLOCKED
    else:
        status = CaseStatus.NEEDS_REVIEW

    summary = None
    if status == CaseStatus.VERIFIED:
        summary = {f.field: f.verified_value for f in fields} | {"source_document_sha256": doc.sha256}
        summary |= {f"{f.field}_derived": f.derived_value for f in fields if f.derived_value}

    return CaseResult(case_id=case["case_id"], status=status, document_type=doc.doc_type, document_file=doc.file,
                      document_sha256=doc.sha256, fields=fields, checks=_layers(doc, raw, fields, plaus),
                      next_action=_next_action(fields, doc), client_safe_summary=summary, rules_version=RULES_VERSION,
                      extractor_version=EXTRACTOR_VERSION, request=case, document_pages=_page_views(doc))


def _page_views(doc: ExtractedDoc, margin: float = 36.0) -> list[dict]:
    """Crop each page to the band that has text, so the preview isn't mostly blank paper."""
    views = []
    for pg in doc.pages:
        lines = [ln for ln in doc.lines if ln["page"] == pg["page"]]
        if not lines:
            views.append({"page": pg["page"], "box": [0, 0, pg["width"], pg["height"]]})
            continue
        left = max(0.0, min(ln["bbox"][0] for ln in lines) - margin)
        right = min(pg["width"], max(ln["bbox"][2] for ln in lines) + margin)
        top = max(0.0, min(ln["bbox"][1] for ln in lines) - margin)
        bottom = min(pg["height"], max(ln["bbox"][3] for ln in lines) + margin)
        views.append({"page": pg["page"], "box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)]})
    return views
