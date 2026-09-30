"""Run the verifier over the synthetic package: python -m app.cli [--json]"""
import json
import sys

from .service import DATA_DIR, verify_all


def main() -> None:
    results = verify_all()
    if "--json" in sys.argv:
        print(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return
    for r in results:
        print(f"\n{r.case_id}  {r.status.value:<13} doc={r.document_type.value}  file={r.document_file}")
        for f in r.fields:
            shown = f.verified_value or f"(not verified) doc={f.document_value}"
            print(f"   {f.field:<12} {f.status.value:<20} {shown}")
        print(f"   -> {r.next_action}")
    out = DATA_DIR.parent / "report.json"
    out.write_text(json.dumps([r.model_dump(mode="json") for r in results], indent=2), encoding="utf-8")
    print(f"\nFull evidence report written to {out}")


if __name__ == "__main__":
    main()
