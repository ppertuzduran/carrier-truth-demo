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

Deciding truth by source authority and failing closed, with deterministic rules first and the LLM only as a verified
fallback.

C001 and C006 justify it. Majority voting would have accepted $450,000 against the document. In C006 all three sources
agree, yet none is an issued document. Because these numbers reach the client in Live View and the carrier in the
submission, a false "verified" costs far more than a "review".

That is why extraction is deterministic (reproducible, auditable, milliseconds, zero token cost), and why the refusal
lives in the data model rather than the UI. The LLM only steps in where the rules don't recognize a label. It must
return a verbatim quote that the code checks before accepting any number: the LLM proposes, the code verifies. This
also keeps token cost under control at scale.

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
