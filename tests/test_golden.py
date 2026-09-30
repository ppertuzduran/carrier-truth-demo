"""Golden tests for the synthetic package, the refusal contract, and robustness beyond the six cases."""
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.extract import Candidate, ExtractedDoc, classify
from app.main import app
from app.models import DocType, FieldResult, FieldStatus
from app.reconcile import check_premium, premium_role, verify_case
from app.service import doc_path, verify_all

EXPECTED = {
    # case: (case status, doc type, {field: status})
    "C001": ("BLOCKED", "ISSUED_ILLUSTRATION", {"client_name": "VERIFIED", "carrier": "VERIFIED",
                                                "coverage": "CONTRADICTED", "premium": "VERIFIED"}),
    "C002": ("VERIFIED", "ISSUED_ILLUSTRATION", {"client_name": "VERIFIED", "carrier": "VERIFIED",
                                                 "coverage": "VERIFIED", "premium": "VERIFIED"}),
    "C003": ("NEEDS_REVIEW", "ISSUED_ILLUSTRATION", {"client_name": "PLACEHOLDER", "carrier": "VERIFIED",
                                                     "coverage": "VERIFIED", "premium": "VERIFIED_NORMALIZED"}),
    "C004": ("VERIFIED", "ISSUED_ILLUSTRATION", {"client_name": "VERIFIED", "carrier": "VERIFIED",
                                                 "coverage": "VERIFIED", "premium": "VERIFIED"}),
    "C005": ("VERIFIED", "ISSUED_ILLUSTRATION", {"client_name": "VERIFIED", "carrier": "VERIFIED",
                                                 "coverage": "VERIFIED", "premium": "VERIFIED"}),
    "C006": ("BLOCKED", "PORTAL_PREVIEW", {"client_name": "UNVERIFIABLE_SOURCE", "carrier": "UNVERIFIABLE_SOURCE",
                                           "coverage": "UNVERIFIABLE_SOURCE", "premium": "UNVERIFIABLE_SOURCE"}),
}

RESULTS = {r.case_id: r for r in verify_all()}
F = lambda case_id, field: next(f for f in RESULTS[case_id].fields if f.field == field)  # noqa: E731


# ---------- the six synthetic cases ----------

@pytest.mark.parametrize("case_id", EXPECTED)
def test_case_verdicts(case_id):
    status, doc_type, fields = EXPECTED[case_id]
    r = RESULTS[case_id]
    assert r.status.value == status
    assert r.document_type.value == doc_type
    assert {f.field: f.status.value for f in r.fields} == fields


def test_c001_document_wins_and_plausibility_flags_it_independently():
    cov = F("C001", "coverage")
    assert cov.document_value == "$50.00" and cov.verified_value is None
    layers = {L.layer: L.status for L in RESULTS["C001"].checks}
    assert layers["L5"] == "fail" and layers["L6"] == "fail"


def test_c003_derived_value_is_labeled_and_original_kept():
    prem = F("C003", "premium")
    assert prem.verified_value == "$720.00 ANNUAL"
    assert prem.derived_value.startswith("$60.00/mo (derived")


def test_c005_base_premium_verified_rider_excluded():
    prem = F("C005", "premium")
    assert prem.verified_value == "$58.00 MONTHLY"
    assert "optional rider $73.00 MONTHLY excluded" in prem.detail
    roles = {e.raw_text: e.role for e in prem.evidence if e.source == "document"}
    assert roles["Premium with Optional Rider: $73.00 MONTHLY"] == "optional_rider"


def test_c006_consistency_is_informational_only():
    layers = {L.layer: L.status for L in RESULTS["C006"].checks}
    assert layers["L1"] == "fail" and all(layers[k] == "skip" for k in ("L3", "L4", "L5", "L6", "L7"))


# ---------- the refusal contract ----------

@pytest.mark.parametrize("case_id", EXPECTED)
def test_never_exposes_unverified_values(case_id):
    r = RESULTS[case_id]
    for f in r.fields:
        if f.status.value not in ("VERIFIED", "VERIFIED_NORMALIZED"):
            assert f.verified_value is None
    assert (r.client_safe_summary is not None) == (r.status.value == "VERIFIED")


def test_every_document_value_has_provenance():
    for r in RESULTS.values():
        for f in r.fields:
            if f.document_value is not None:
                doc_ev = [e for e in f.evidence if e.source == "document"]
                assert doc_ev and all(e.bbox and e.page and e.doc_sha256 for e in doc_ev)


def test_contract_rejects_verified_value_on_unverified_status():
    with pytest.raises(ValueError):
        FieldResult(field="coverage", status=FieldStatus.CONTRADICTED, verified_value="$1.00", reason="x")


# ---------- robustness beyond the six cases ----------

def test_sources_agreeing_on_an_implausible_value_are_still_blocked():
    # Same C001 document, but the request ALSO says $50: reconciliation alone would verify it.
    r = verify_case({"case_id": "X1", "requested_name": "Ana Rivera", "requested_coverage": 50, "ui_premium": 62.0},
                    doc_path("C001"))
    cov = next(f for f in r.fields if f.field == "coverage")
    assert cov.status == FieldStatus.IMPLAUSIBLE and cov.verified_value is None
    assert r.status.value == "BLOCKED"


def _doc_with_premiums(*labels_amounts):
    cands = [Candidate(field="premium", value=f"${a:,.2f} MONTHLY", raw_text=f"{lbl}: ${a:,.2f} MONTHLY", page=1,
                       bbox=[0, 0, 1, 1], amount=Decimal(str(a)), freq="MONTHLY", label=lbl) for lbl, a in labels_amounts]
    return ExtractedDoc(file="x.pdf", sha256="0" * 64, doc_type=DocType.ISSUED_ILLUSTRATION, doc_type_reason="t",
                        candidates={"premium": cands})


def test_unknown_premium_label_is_not_guessed():
    doc = _doc_with_premiums(("Premium", 58), ("Target Premium", 90))
    f = check_premium({"case_id": "X2", "ui_premium": 58.0}, doc)
    assert f.status == FieldStatus.AMBIGUOUS and "Target Premium" in f.reason


def test_ui_showing_rider_premium_needs_rider_confirmation():
    doc = _doc_with_premiums(("Premium", 58), ("Premium with Optional Rider", 73))
    f = check_premium({"case_id": "X3", "ui_premium": 73.0}, doc)
    assert f.status == FieldStatus.AMBIGUOUS and "rider" in f.reason


def test_different_base_premiums_are_ambiguous():
    doc = _doc_with_premiums(("Premium", 58), ("Base Premium", 61))
    assert check_premium({"case_id": "X4", "ui_premium": 58.0}, doc).status == FieldStatus.AMBIGUOUS


@pytest.mark.parametrize("line", ["Quote Preview", "This is an estimate only", "DRAFT", "Not an official document",
                                  "Specimen illustration"])
def test_disclaimers_remove_authority_even_with_a_carrier_title(line):
    lines = [{"text": "Carrier Illustration"}, {"text": line}]
    assert classify(lines)[0] == DocType.PORTAL_PREVIEW


def test_document_without_known_title_is_not_authoritative():
    assert classify([{"text": "Policy Summary"}, {"text": "Face Amount: $1.00"}])[0] == DocType.UNKNOWN


@pytest.mark.parametrize("label,role", [("Premium", "base"), ("Base Premium", "base"),
                                        ("Premium with Optional Rider", "optional_rider"),
                                        ("Rider Premium", "optional_rider"), ("Target Premium", "unknown")])
def test_premium_role_taxonomy(label, role):
    assert premium_role(label) == role


# ---------- API ----------

def test_api_and_evidence_image():
    client = TestClient(app)
    assert client.get("/api/cases").status_code == 200
    img = client.get("/api/evidence/C001/coverage.png")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert client.get("/api/documents/C005/page/1.png").status_code == 200


def test_upload_endpoint_removed():
    assert TestClient(app).post("/api/verify").status_code in (404, 405)
