from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from .models import CaseResult
from .reconcile import verify_case

DATA_DIR = Path(os.environ.get("CARRIER_TRUTH_DATA", Path(__file__).resolve().parent.parent / "data"))


def load_requests() -> list[dict]:
    return json.loads((DATA_DIR / "quote_requests.json").read_text(encoding="utf-8"))


def doc_path(case_id: str) -> Path:
    return DATA_DIR / "documents" / f"{case_id}.pdf"


@lru_cache(maxsize=1)
def verify_all() -> list[CaseResult]:
    return [verify_case(c, doc_path(c["case_id"])) for c in load_requests()]
