from __future__ import annotations

import os
import threading
from typing import Any, Dict

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response


app = FastAPI(title="Contract Local API", version="0.1.0")
_SESSION = requests.Session()
_LOCK = threading.Lock()

DJANGO_BASE = (os.environ.get("LOCAL_API_DJANGO_BASE") or "http://127.0.0.1:8000/contract").rstrip("/")
WORKER_HEALTH_URL = (os.environ.get("LOCAL_API_WORKER_HEALTH") or "http://127.0.0.1:8001/healthz").rstrip("/")
REQUEST_TIMEOUT = int(os.environ.get("LOCAL_API_TIMEOUT", "30"))


def _django(path: str) -> str:
    return f"{DJANGO_BASE}{path}"


def _safe_json(resp: requests.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"ok": False, "error": resp.text[:400]}


def _ensure_csrf() -> str:
    with _LOCK:
        resp = _SESSION.get(_django("/api/health/"), timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 500:
        raise RuntimeError(f"django health failed: {resp.status_code}")
    token = _SESSION.cookies.get("csrftoken", "")
    if token:
        return token
    return "local-api-csrf"


def _proxy_get_json(path: str) -> JSONResponse:
    with _LOCK:
        resp = _SESSION.get(_django(path), timeout=REQUEST_TIMEOUT)
    return JSONResponse(status_code=resp.status_code, content=_safe_json(resp))


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "local_api"}


@app.get("/contract/api/health/")
def contract_health():
    django_ok = False
    worker_ok = False
    try:
        with _LOCK:
            r = _SESSION.get(_django("/api/health/"), timeout=5)
        django_ok = r.status_code < 500
    except Exception:
        django_ok = False
    try:
        r2 = requests.get(WORKER_HEALTH_URL, timeout=5)
        worker_ok = r2.status_code < 500
    except Exception:
        worker_ok = False

    payload = {"ok": django_ok, "service": "local_api", "upstream": {"django": django_ok, "worker": worker_ok}}
    resp = JSONResponse(status_code=200 if django_ok else 503, content=payload)
    resp.set_cookie("csrftoken", "local-api-csrf")
    return resp


@app.post("/contract/api/start/")
async def contract_start(file: UploadFile = File(...)):
    filename = (file.filename or "uploaded.pdf").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="only PDF is supported")

    try:
        csrf = _ensure_csrf()
        content = await file.read()
        files = {"file": (filename, content, file.content_type or "application/pdf")}
        with _LOCK:
            resp = _SESSION.post(
                _django("/api/start/"),
                files=files,
                headers={"X-CSRFToken": csrf},
                timeout=max(REQUEST_TIMEOUT, 120),
            )
        return JSONResponse(status_code=resp.status_code, content=_safe_json(resp))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"proxy start failed: {exc}") from exc


@app.get("/contract/api/status/{job_id}/")
def contract_status(job_id: int):
    return _proxy_get_json(f"/api/status/{job_id}/")


@app.get("/contract/api/result/{job_id}/")
def contract_result(job_id: int):
    return _proxy_get_json(f"/api/result/{job_id}/")


@app.get("/contract/api/export_pdf/{job_id}/")
def contract_export_pdf(job_id: int):
    with _LOCK:
        resp = _SESSION.get(_django(f"/api/export_pdf/{job_id}/"), timeout=max(REQUEST_TIMEOUT, 120))
    if resp.status_code != 200:
        return JSONResponse(status_code=resp.status_code, content=_safe_json(resp))
    return Response(content=resp.content, media_type="application/pdf")

