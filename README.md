# Carrier Truth: La Verdad del Documento del Carrier

Extracts **client, carrier, coverage and premium** from carrier documents, shows **exactly where each value came from**
(file hash, page, bounding box, raw line), and **refuses to present a value as verified** when there is a material
contradiction, an unresolved ambiguity, an implausible value, or when the document is not an issued carrier document.

> **Rule implemented:** the source of truth for money and coverage is the **issued carrier document**, never the
> requested value or the value shown by an interface.

All data in `data/` is the synthetic package provided for the challenge. No real client data is used.

---

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate              # Windows · use: source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload       # open http://localhost:8000
```

Other entry points:

```bash
python -m app.cli                   # verdicts in the console + writes report.json (full evidence)
pytest -q                           # 35 tests: golden cases, refusal contract, robustness beyond the 6 cases
```

---

## How to use the demo

1. **Pick a case** in the left panel (C001–C006). The dots show the status of client · carrier · coverage · premium.
2. **Read the verdict.** The frame around the case is colored by the result: green = **VERIFIED**, amber =
   **NEEDS REVIEW**, red = **BLOCKED**. Next to it, the *next action* tells the agent what to do.
3. **Read the pipeline strip.** Seven layers (L1–L7) show which check passed (✓), warned (!), failed (✕) or was
   skipped (–). Hover a layer to see the exact tool behind it.
4. **Look at the four tiles.** Each shows the value the system is willing to stand behind. A **struck-through**
   value is *not verified*. Small print under a value explains qualifiers, e.g. *"base premium · optional rider
   excluded"* or *"↳ $60.00/mo (derived: $720.00 ANNUAL ÷ 12)"*.
5. **Click a tile to trace it (focus mode).** The case opens already focused on its first problematic field.
   - The **carrier document** (left) and the **API payload** (right) blur and dim.
   - The exact lines the value came from stay sharp and highlighted, colored by their role.
   - **Numbered notes** (center) explain each source. Lines connect every note to what it talks about. A conflict note
     connects to **both** conflicting values.
   - Hover a note to isolate its connections. Click the tile again, or press <kbd>Esc</kbd>, to leave focus mode.
6. **Check the bottom panels.** *What PBG Live View may show the client* is populated **only** for verified cases;
   otherwise it is withheld. *Audit trail* shows the document hash, extractor and rules versions, and a link to the
   full JSON evidence (`/api/cases/{id}`).
7. **Theme:** the UI follows your OS light/dark setting. The **◐ Theme** button switches it (remembered per browser).

### Colors

| Color | Meaning |
|---|---|
| Green | Verified from the carrier document, or a claim that agrees with it |
| Blue | The carrier document as source of truth in a conflict |
| Red | Contradiction, non-authoritative source, placeholder or implausible value |
| Amber | Ambiguity, or a derived (converted) value |
| Grey, dashed line | Context: the source is readable, but it doesn't decide anything |

---

## Why the demo shows client · carrier · coverage · premium

These four fields are the minimum a quote needs before anyone can show it to a client or submit it to a carrier:
**who** is insured (client), **who** insures (carrier), **how much protection** (coverage / face amount) and **what it
costs** (premium). If any of them is wrong, the illustration shown in Live View, the client's recorded choice and the
application sent to the carrier are wrong too. Each tile is therefore a yes/no question: *can we stand behind this
value?*

The three panes answer *why*:

- **Carrier document:** the only source of truth for money and coverage, but only if it is an **issued**
  illustration.
- **Quote request / API payload:** `requested_name`, `requested_coverage`, `ui_premium`. These are **claims** to check
  against the document, never used to fill or override it. The payload has no carrier field, so the carrier can only
  come from the document.
- **Notes:** the reasoning, tied to the exact evidence.

---

## What happens when you select each field

| Field | What gets highlighted | Criteria |
|---|---|---|
| **Client** | `Applicant:` line on the document + `requested_name` | Names are compared after removing accents, case and extra spaces; similarity ≥ 0.92 → verified. A template default (`Client`, `Applicant`, `Insured`, …) is a **placeholder**: identity can't be tied to the document → review. A different name → contradicted. |
| **Carrier** | `Carrier:` line on the document | Single source (the payload has none). Verified only if the document itself is authoritative. |
| **Coverage** | `Face Amount:` line + `requested_coverage` | Exact comparison with `Decimal` money (tolerance $0.01, rounding only). Any difference → **contradicted**: the document value is shown as truth but not verified, and the request never overrides it. Then a plausibility check: face amount below $10,000, or annual premium above 10% of the face amount → **implausible**, even if every source agrees. |
| **Premium** | Every premium line on the document + `ui_premium` | Each line gets a **semantic role** first: base (`Premium`, `Base Premium`, …), optional rider (`…with Optional Rider`, `Rider Premium`, …) or unknown. Base is compared with the UI; riders are excluded unless elected; unknown labels are never guessed. The payment mode is normalized: an annual premium that equals the UI only after ÷ 12 is verified **as annual**, and the monthly figure is shown as **derived**. |

### How each type of ambiguity is resolved

| Type | Example | Detection (tool) | Decision |
|---|---|---|---|
| **Contradiction** | C001: document $50 vs request $450,000 | Deterministic comparison, `Decimal`, $0.01 tolerance | Refuse to verify. The document is shown as truth; the next action is to re-run the illustration. |
| **Identity / placeholder** | C003: applicant `"Client"` | Canonicalized name + placeholder list + similarity ratio | Needs review: regenerate the illustration with the legal name. |
| **Payment-mode normalization** | C003: `$720 ANNUAL` vs UI `60` | Frequency captured by the extractor; conversion only for comparison | Verify the carrier's **annual** figure; label $60/mo as **derived**. Never present it as a quoted monthly premium, since carriers may price monthly modes differently. |
| **Semantic ambiguity** | C005: `Premium $58`, `Base Premium $58`, `Premium with Optional Rider $73` | Label → role taxonomy; duplicate statements of the same amount collapse | Verify the **$58 base premium**; the $73 is an optional rider, shown and explicitly **excluded**. If the UI showed $73 → review (rider election must be confirmed). Two different *base* premiums or an unknown label → ambiguous, never guessed. |
| **Source validity** | C006: "Portal Print View … NOT an issued illustration" | Document classifier: a disclaimer anywhere beats the title; a known title is required | Nothing is verified, even though every number agrees. Consistency on a non-authoritative source is informational only. |
| **Implausible agreement** | C001 document + a request that also says $50 (tested) | Domain bounds (face ≥ $10k, premium/face ratio) | Block: two sources agreeing on an impossible value prove nothing. |

---

## The verification pipeline (L1–L7)

Each layer answers one question with the cheapest reliable tool. The LLM layer is last and narrow on purpose.

| Layer | Question | Tool | Why this tool |
|---|---|---|---|
| **L1 Source authority** | Is this an issued carrier document? | Deterministic classifier: required title + disclaimer markers (a disclaimer beats any title) | Authority is a compliance decision; it must be explainable and cannot be "granted" by a model |
| **L2 Extraction + provenance** | Where is each value, exactly? | pdfplumber text lines + label-anchored regex; every candidate kept with page, bbox and SHA-256 | Born-digital PDFs have a text layer: exact, reproducible, millisecond extraction |
| **L3 Semantic roles** | What does each value *mean*? | Label → role taxonomy (base / optional rider / unknown) | Resolves C005 without guessing; unknown labels are escalated, not interpreted |
| **L4 Normalization** | Are values comparable? | `Decimal` money, payment-mode conversion (labeled derived), name canonicalization | No float drift; conversions are visible, never silent |
| **L5 Cross-source reconciliation** | Do the claims agree with the truth? | Authority order document > request > UI; tolerances; placeholder detection | Fixed authority order instead of majority voting (C001 and C006 would fail with voting) |
| **L6 Plausibility** | Is the agreed value possible at all? | Domain bounds | Catches errors that every source repeats |
| **L7 LLM adjudication** | Only for what L2–L3 can't recognize | Claude structured output; must return a verbatim quote + page, and code checks the quote exists before parsing the number | *The LLM proposes, the code verifies.* Designed and routed; not called in this demo, because every label in the package is recognized deterministically (zero token cost) |

A case is **VERIFIED** only if all four fields are verified. **BLOCKED** if any field is contradicted, implausible,
missing, or the source is not authoritative. **NEEDS REVIEW** otherwise (placeholder or ambiguity).

**The refusal is enforced by the data contract, not by the UI.** `FieldResult` (Pydantic) raises an error if a
`verified_value` is set on a non-verified status, and `client_safe_summary` exists only for fully verified cases. No
front end can show an unverified number by accident.

---

## Results on the synthetic package

| Case | Verdict | Why |
|---|---|---|
| C001 | **BLOCKED** | Document face amount **$50.00** vs **$450,000** requested (L5). Also fails plausibility on its own (L6). Premium $62/mo matches. |
| C002 | **VERIFIED** | Control case: everything matches the carrier document. |
| C003 | **NEEDS REVIEW** | Applicant is the placeholder `"Client"`. Premium verified as **$720.00 ANNUAL**; $60/mo shown as derived. |
| C004 | **VERIFIED** | Control case: everything matches. |
| C005 | **VERIFIED** | Base premium **$58.00/mo** verified; optional rider **$73.00/mo** identified and excluded. |
| C006 | **BLOCKED** | Portal preview, not an issued illustration: nothing verified although all values agree. |

---

## API

- `GET /api/cases`: verdicts, pipeline checks, notes and evidence for every case
- `GET /api/cases/{case_id}`: one case
- `GET /api/evidence/{case_id}/{field}.png`: the PDF region a value came from, highlighted
- `GET /api/documents/{case_id}/page/{n}.png`: page preview cropped to its text (used by the UI)
- `GET /api/documents/{case_id}.pdf`: the original document
- Interactive docs: `/docs`

Verifying arbitrary new documents is intentionally **not exposed** in this demo: the extraction templates are tuned to
the synthetic layout, and an upload feature would invite untested inputs. See *Scaling* below.

## Project layout

```
app/
  extract.py     L1 classifier + L2 extraction with provenance
  reconcile.py   L3–L6 rules, pipeline summary (L7 routing), verdict gate
  annotate.py    notes tied to evidence (a conflict note targets both sides)
  models.py      evidence contract (refusal enforced by validation)
  evidence.py    page rendering and highlight crops
  main.py        FastAPI
  static/        single-page UI (no build step)
tests/           golden, contract and robustness tests
data/            synthetic package (quote_requests.json + PDFs)
```

## Key decisions and what was rejected

| Decision | Why | Rejected |
|---|---|---|
| Deterministic extraction first | Compliance data: reproducible, auditable, no token cost | LLM/vision-only extraction: can invent numbers, varies between runs, costs tokens on every page × quote × agent |
| Authority-based truth | The business rule names the source of truth | Majority voting |
| Fail closed | A false "verified" reaches the client and the carrier; a review costs seconds | "Most likely" value |
| Keep every candidate + semantic roles | Ambiguity must be visible and resolved by meaning, not by position | First-match extraction |
| Provenance in the data model | Audits, client disputes, reproducibility | Plain values |
| pdfplumber (MIT) + pypdfium2 (Apache/BSD) | Positions + rendering, permissive licenses | PyMuPDF (AGPL), OCR (adds errors on born-digital PDFs) |
| No RAG / vector DB | Exact field extraction, not search | Embedding retrieval |

**Theory behind it:** W3C PROV-DM (provenance) · truth discovery and data fusion, here with a fixed authority order ·
wrapper induction (Kushmerick, 1997) for per-carrier templates · Fellegi–Sunter record linkage (names) · Chow's
reject option (1970) for abstaining when uncertain.

## Scaling toward PBG Copilot

- **Carrier template registry** per carrier and document version (anchors, summary-page locator), with golden tests
  built from specimen documents. When a template misses, L7 proposes; a human approves new templates.
- **Where it plugs into Copilot:** a gate after *Cotizar* before *Vista en Vivo* shows "Página resumen" or
  "Comparación"; a gate before *Enviar a la aseguradora*; a read-back of the carrier portal after autofill compared
  with the document; the client's recorded choice tied to the document hash.
- **Discrepancy rate per carrier and field**, to detect when a carrier changes its portal or layout.
- **Scale:** stateless workers behind a queue, Postgres (JSONB evidence), object storage keyed by hash. LLM spend only
  when a template misses.

## Known limitations

Templates are tuned to this synthetic layout. Real 30+ page illustrations would fail safe (MISSING or AMBIGUOUS) but
noisily. There is no OCR path for scanned documents. Authority detection depends on known markers. Plausibility
bounds and the placeholder list are generic, not per carrier or product. The input contract lacks rider election,
payment mode and product. L7 is designed and routed but not implemented. More detail in [ANSWERS.md](ANSWERS.md).

## Other deliverables

- Written answers: [ANSWERS.md](ANSWERS.md)
- Screen-sharing research: [SCREEN_SHARING.md](SCREEN_SHARING.md)
