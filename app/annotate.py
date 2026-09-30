"""Turn a field verdict into callouts, each pointing at the evidence it talks about.

A conflict note targets *both* sides of the conflict, so the UI can draw it connected to both values.
"""
from __future__ import annotations

from .models import Evidence, FieldResult, FieldStatus, Note

LABEL = {"client_name": "client", "carrier": "carrier", "coverage": "coverage", "premium": "premium"}
CLASSIFIER = "doc-classifier-v1"


def _key(e: Evidence) -> str:
    return e.locator.rsplit(".", 1)[-1]


def _value_of(e: Evidence) -> str:
    return e.raw_text.split(":", 1)[1].strip() if ":" in e.raw_text else e.raw_text


def _claim(e: Evidence) -> str:
    return f"{_key(e)}: {e.raw_text}"


def annotate(f: FieldResult) -> list[Note]:
    ev = f.evidence
    docs = [i for i, e in enumerate(ev) if e.source == "document" and e.method != CLASSIFIER]
    markers = [i for i, e in enumerate(ev) if e.method == CLASSIFIER]
    claims = [i for i, e in enumerate(ev) if e.source == "quote_request"]
    what = LABEL.get(f.field, f.field)
    s = f.status
    notes: list[Note] = []

    base = [i for i in docs if ev[i].role != "optional_rider"]
    riders = [i for i in docs if ev[i].role == "optional_rider"]

    if s == FieldStatus.VERIFIED:
        if base:
            lines = " and ".join(f"“{ev[i].raw_text}”" for i in base)
            title = "Verified base premium" if riders else "Verified from the carrier document"
            notes.append(Note(tone="ok", title=title, targets=base,
                              text=f"Page {ev[base[0]].page}: {lines}. Source of truth for the {what}."))
        for i in riders:
            notes.append(Note(tone="info", title="Optional rider premium: excluded", targets=[i],
                              text=f"“{ev[i].raw_text}” is an add-on option, not the policy premium. The quote does "
                                   "not elect the rider, so it is kept as an alternative and not presented as the premium."))
        for i in claims:
            notes.append(Note(tone="ok", title="Matches the API payload", targets=[i],
                              text=f"{_claim(ev[i])} agrees with the carrier document."))
        if not claims:
            notes.append(Note(tone="info", title="Single source", targets=docs,
                              text=f"The quote request has no {what} field, so it can only come from the carrier document."))

    elif s == FieldStatus.VERIFIED_NORMALIZED:
        for i in docs:
            notes.append(Note(tone="ok", title="Verified from the carrier document", targets=[i],
                              text=f"“{ev[i].raw_text}”. The original carrier figure is what gets presented."))
        for i in claims:
            notes.append(Note(tone="warn", title="Derived value, labeled as derived", targets=[i] + docs,
                              text=f"{_claim(ev[i])} equals the carrier figure only after conversion: {f.derived_value}. "
                                   "It is shown as derived, never as the carrier's quoted monthly premium."))

    elif s == FieldStatus.CONTRADICTED:
        for i in docs:
            notes.append(Note(tone="truth", title="Carrier document: source of truth", targets=[i],
                              text=f"Page {ev[i].page}: “{ev[i].raw_text}”."))
        for i in claims:
            notes.append(Note(tone="bad", title="Claim that contradicts the document", targets=[i],
                              text=f"{_claim(ev[i])}. A request or UI value never overrides the carrier document."))
        notes.append(Note(tone="bad", title=f"Conflict: {f.document_value} ≠ {f.requested_value or f.ui_value}",
                          targets=docs + claims, text=f"Material mismatch on {what}: refusing to present it as verified."))

    elif s == FieldStatus.AMBIGUOUS:
        by_value: dict[str, list[int]] = {}
        for i in docs:
            by_value.setdefault(_value_of(ev[i]), []).append(i)
        for n, (value, idxs) in enumerate(by_value.items(), start=1):
            labels = " and ".join(f"“{ev[i].raw_text.split(':', 1)[0]}”" for i in idxs)
            notes.append(Note(tone="warn", title=f"Option {n} on the carrier document: {value}", targets=idxs,
                              text=f"Stated as {labels}."))
        for i in claims:
            notes.append(Note(tone="info", title="What the UI shows", targets=[i],
                              text=f"{_claim(ev[i])}. It matches one option, but the UI can't decide which option the "
                                   "client elected."))
        notes.append(Note(tone="warn", title=f"{len(by_value)} different {what}s on one document", targets=docs + claims,
                          text="The request doesn't say which option was elected (e.g. with or without the rider). "
                               "Picking one would be a guess, so nothing is verified."))

    elif s == FieldStatus.PLACEHOLDER:
        for i in docs:
            notes.append(Note(tone="bad", title="Placeholder instead of a name", targets=[i],
                              text=f"“{ev[i].raw_text}”: a template default, not the insured's legal name."))
        for i in claims:
            notes.append(Note(tone="info", title="Requested client", targets=[i],
                              text=f"{_claim(ev[i])} cannot be tied to this document."))
        notes.append(Note(tone="warn", title="Identity not verified", targets=docs + claims,
                          text="Nothing proves this illustration was issued for this client. Regenerate it with the legal name."))

    elif s == FieldStatus.UNVERIFIABLE_SOURCE:
        for i in markers:
            notes.append(Note(tone="bad", title="Not an issued carrier document", targets=[i],
                              text=f"“{ev[i].raw_text}”. An interface preview has no authority over money or coverage."))
        for i in docs:
            notes.append(Note(tone="info", title="Readable, but not authoritative", targets=[i],
                              text=f"“{ev[i].raw_text}” comes from a portal view, not the carrier's document."))
        for i in claims:
            notes.append(Note(tone="info", title="Agreement is not verification", targets=[i],
                              text=f"{_claim(ev[i])}. Matching values from non-authoritative sources prove nothing."))
        notes.append(Note(tone="bad", title="Not verified: no authoritative source", targets=markers + docs + claims,
                          text="Only an issued carrier document can verify this. Download the official illustration."))

    elif s == FieldStatus.IMPLAUSIBLE:
        for i in docs:
            notes.append(Note(tone="bad", title="Fails domain checks", targets=[i],
                              text=f"“{ev[i].raw_text}” is not a plausible value for a life policy."))
        for i in claims:
            notes.append(Note(tone="info", title="Agreement is not enough", targets=[i],
                              text=f"{_claim(ev[i])} agrees, but two sources agreeing on an impossible value prove nothing."))
        notes.append(Note(tone="bad", title="Not verified: implausible", targets=docs + claims, text=f.reason))

    else:  # MISSING
        notes.append(Note(tone="bad", title=f"No {what} found", targets=list(range(len(ev))), text=f.reason))

    return notes
