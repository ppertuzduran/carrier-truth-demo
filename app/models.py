"""Evidence contract.

The key rule lives here, not in the UI: a field can only expose `verified_value`
when its status is a verified status. Anything else carries the raw claims
(document / requested / UI) plus the reason, never a "verified" number.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator


class DocType(str, Enum):
    ISSUED_ILLUSTRATION = "ISSUED_ILLUSTRATION"  # authoritative carrier document
    PORTAL_PREVIEW = "PORTAL_PREVIEW"            # an interface view, not a carrier document
    UNKNOWN = "UNKNOWN"


class FieldStatus(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_NORMALIZED = "VERIFIED_NORMALIZED"  # same money, different payment mode
    AMBIGUOUS = "AMBIGUOUS"                      # authoritative doc holds >1 candidate
    CONTRADICTED = "CONTRADICTED"                # material mismatch vs the carrier doc
    PLACEHOLDER = "PLACEHOLDER"                  # doc has a generic value (e.g. "Client")
    UNVERIFIABLE_SOURCE = "UNVERIFIABLE_SOURCE"  # document is not authoritative
    IMPLAUSIBLE = "IMPLAUSIBLE"                  # sources agree, but the value fails domain bounds
    MISSING = "MISSING"


VERIFIED_STATUSES = {FieldStatus.VERIFIED, FieldStatus.VERIFIED_NORMALIZED}


class CaseStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"  # nothing contradicts, but something can't be confirmed
    BLOCKED = "BLOCKED"            # material contradiction or no authoritative source


class Evidence(BaseModel):
    source: str                        # "document" | "quote_request"
    locator: str                       # file#page / json path
    raw_text: str
    page: Optional[int] = None
    bbox: Optional[list[float]] = None  # [x0, top, x1, bottom] in PDF points
    doc_sha256: Optional[str] = None
    method: str
    role: Optional[str] = None          # semantic role of a document line, e.g. "base" / "optional_rider"


class Note(BaseModel):
    """A human-readable callout attached to one or more evidence items (by index into FieldResult.evidence)."""
    tone: str        # ok | truth | bad | warn | info
    title: str
    text: str
    targets: list[int]


class FieldResult(BaseModel):
    field: str
    status: FieldStatus
    verified_value: Optional[str] = None
    document_value: Optional[str] = None
    requested_value: Optional[str] = None
    ui_value: Optional[str] = None
    derived_value: Optional[str] = None  # computed from the document (e.g. annual -> monthly), always labeled as derived
    detail: Optional[str] = None         # short qualifier shown with the value (e.g. "base premium, rider excluded")
    reason: str
    evidence: list[Evidence] = []
    notes: list[Note] = []

    @model_validator(mode="after")
    def _refuse_unverified(self):
        if self.status not in VERIFIED_STATUSES and self.verified_value is not None:
            raise ValueError(f"{self.field}: verified_value is not allowed with status {self.status}")
        if self.status in VERIFIED_STATUSES and self.verified_value is None:
            raise ValueError(f"{self.field}: verified status requires verified_value")
        return self


class LayerCheck(BaseModel):
    """One layer of the verification pipeline, with the tool that implements it."""
    layer: str
    name: str
    tool: str
    status: str   # pass | warn | fail | skip
    detail: str


class CaseResult(BaseModel):
    case_id: str
    status: CaseStatus
    document_type: DocType
    document_file: Optional[str]
    document_sha256: Optional[str]
    fields: list[FieldResult]
    checks: list[LayerCheck] = []
    next_action: str
    # Only present when the whole case is VERIFIED: what may be shown to a client (e.g. PBG Live View).
    client_safe_summary: Optional[dict] = None
    rules_version: str
    extractor_version: str
    request: dict = {}
    # Visible region of each page for the preview: {"page", "box": [x0, top, x1, bottom]} in PDF points.
    document_pages: list[dict] = []
