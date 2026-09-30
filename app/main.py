from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from .evidence import render_highlight, render_page
from .models import CaseResult
from .service import doc_path, load_requests, verify_all

app = FastAPI(title="Carrier Truth", version="0.1.0",
              description="Verifies client, carrier, coverage and premium against the carrier document, with provenance.")
STATIC = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/cases", response_model=list[CaseResult])
def cases():
    return verify_all()


@app.get("/api/cases/{case_id}", response_model=CaseResult)
def case(case_id: str):
    for r in verify_all():
        if r.case_id == case_id:
            return r
    raise HTTPException(404, "Unknown case")


@app.get("/api/requests")
def requests_():
    return load_requests()


@app.get("/api/evidence/{case_id}/{field}.png")
def evidence(case_id: str, field: str):
    result = next((r for r in verify_all() if r.case_id == case_id), None)
    pdf = doc_path(case_id)
    if result is None:
        raise HTTPException(404, "Unknown case")
    f = next((f for f in result.fields if f.field == field), None)
    if f is None:
        raise HTTPException(404, "Unknown field")
    doc_ev = [e for e in f.evidence if e.source == "document" and e.bbox]
    if not doc_ev:
        raise HTTPException(404, "No document evidence for this field")
    png = render_highlight(pdf, doc_ev[0].page, [e.bbox for e in doc_ev if e.page == doc_ev[0].page], f.status.value)
    return Response(png, media_type="image/png")


@app.get("/api/documents/{case_id}/page/{page}.png")
def page_preview(case_id: str, page: int):
    result = next((r for r in verify_all() if r.case_id == case_id), None)
    if result is None:
        raise HTTPException(404, "Unknown case")
    view = next((v for v in result.document_pages if v["page"] == page), None)
    if view is None:
        raise HTTPException(404, "Unknown page")
    pdf = doc_path(case_id)
    return Response(render_page(pdf, page, view["box"]), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/documents/{case_id}.pdf")
def document(case_id: str):
    pdf = doc_path(case_id)
    if not pdf.exists():
        raise HTTPException(404)
    return FileResponse(pdf, media_type="application/pdf")


@app.get("/api/report.json", include_in_schema=False)
def report():
    return Response(json.dumps([r.model_dump(mode="json") for r in verify_all()], indent=2),
                    media_type="application/json")
