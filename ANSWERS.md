# Written answers

## 1. What did you build?

Carrier Truth, a verifier (FastAPI + pdfplumber) that extracts client, carrier, coverage and premium from carrier
illustrations and proves where each value came from: SHA-256 hash, page, coordinates and original line, highlighted on
screen. It runs a 7-layer pipeline:
- source authority
- extraction with provenance
- semantic roles (base premium vs. optional rider)
- normalization (derived values are labeled)
- reconciliation (document > request > UI)
- domain plausibility
- LLM adjudication, only when the rules don't recognize something

Results:
- **Verified:** C002, C004 and C005 (the $73 rider premium is excluded).
- **Review:** C003 (applicant named "Client"; $60/mo labeled as derived from $720 annual).
- **Blocked:** C001 (coverage $50 vs. $450,000) and C006 (portal preview with no authority).

The refusal lives in the data contract: an unverified field cannot expose a verified value or reach Live View. It
comes with 35 automated tests.

## 2. What was your most important technical decision, and why?

**Not letting an LLM decide money.** The challenge encouraged AI tools, and the fastest path would have been to hand
the PDF to a model and ask for the four fields. I didn't, for money and coverage. A model can be wrong or give a
different number on each run, it leaves no clear audit trail, and it costs tokens on every page × quote × agent.

So the pipeline is **deterministic first**: reproducible, auditable, milliseconds, zero token cost. The LLM is kept
for what the rules don't recognize, and even then it must return a verbatim quote and page that the code checks
before any number is accepted: **the LLM proposes, the code verifies.** The refusal is enforced in the data model,
not the UI, so an unverified value cannot reach Live View.

The statement only asked to refuse on important contradictions. I extended the refusal to cases that aren't
contradictions: a non-issued source (C006), a placeholder name (C003), and sources agreeing on an implausible value.
And I chose to interpret rather than block when meaning is clear: C005's base premium is verified and the rider is
excluded; C003's annual premium stays the truth, with the monthly figure labeled as derived.

## 3. What could break if this went to production tomorrow?

- **Real formats:** templates match this synthetic layout. 30+ page illustrations, with premiums by mode, by year and
  guaranteed vs. non-guaranteed, would return MISSING or AMBIGUOUS: safe, but noisy.
- **Scanned PDFs:** there is no OCR path.
- **Authority detection:** it depends on known phrases, so a non-issued document without a recognizable disclaimer
  could pass.
- **Generic rules:** plausibility bounds and the placeholder list are not per carrier or product.
- **Names:** second surnames or nicknames can create false name conflicts.
- **Input contract:** it lacks rider election, payment mode and product, so valid cases would land in review.
- **Operations:** no authentication, no persistent audit store, no PII or retention controls, no size limits, and
  results are cached in memory.
- **LLM layer:** designed and routed, but not implemented.

## 4. If you had one more week, what would you build next?

1. **Per-carrier template registry and version tracking** (National Life, Mutual of Omaha, F&G, Americo), with a
   summary-page locator and tests built from real specimen documents.
2. **A real LLM layer:** Claude with structured output returns the value, a verbatim quote and the page; the code
   validates the quote and parses the number itself. Templates it proposes go through human approval.
3. **Copilot integration:**
   - a gate before Live View and before "Enviar a la aseguradora"
   - a read-back of the carrier portal after autofill, compared against the document
   - the client's recorded choice tied to the document hash
4. **Richer input contract:** elected rider, payment mode and product.
5. **Postgres with auditable evidence, authentication, and a discrepancy-rate dashboard per carrier and field** that
   alerts when a carrier changes its portal or document format.
