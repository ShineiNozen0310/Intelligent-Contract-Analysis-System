from __future__ import annotations

import io
import json
import os
import re
import socket
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, Response


app = FastAPI(title="Contract Local API", version="0.3.0")
_SESSION = requests.Session()
_LOCK = threading.Lock()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DJANGO_BASE = (os.environ.get("LOCAL_API_DJANGO_BASE") or "http://127.0.0.1:8000/contract").rstrip("/")
WORKER_HEALTH_URL = (os.environ.get("LOCAL_API_WORKER_HEALTH") or "http://127.0.0.1:8001/healthz").rstrip("/")
REQUEST_TIMEOUT = int(os.environ.get("LOCAL_API_TIMEOUT", "30"))
UPDATE_MANIFEST_URL = (os.environ.get("APP_UPDATE_MANIFEST_URL") or "").strip()
VERSION_FILE = Path((os.environ.get("APP_VERSION_FILE") or str(PROJECT_ROOT / "VERSION")).strip().strip('"').strip("'"))


def _django(path: str) -> str:
    return f"{DJANGO_BASE}{path}"


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _extract_html_error(resp: requests.Response) -> str:
    text = (resp.text or "").strip()
    if not text:
        return "上游服务返回空响应。"

    title = ""
    m = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()

    if title:
        return f"上游服务异常（HTTP {resp.status_code}）：{title}"
    return f"上游服务返回了非 JSON 响应（HTTP {resp.status_code}）。"


def _safe_json(resp: requests.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"ok": False, "error": _extract_html_error(resp)}
def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    detail: str | None = None,
    suggestions: Iterable[str] | None = None,
) -> JSONResponse:
    payload: Dict[str, Any] = {
        "ok": False,
        "error": message,
        "error_code": code,
        "error_message": message,
    }
    if detail:
        payload["error_detail"] = str(detail)[:800]
    fixes = [s for s in (suggestions or []) if str(s).strip()]
    if fixes:
        payload["suggestions"] = fixes
    return JSONResponse(status_code=status_code, content=payload)


def _normalize_upstream_error(
    data: Dict[str, Any],
    *,
    fallback_code: str,
    fallback_message: str,
    suggestions: Iterable[str] | None = None,
) -> Dict[str, Any]:
    payload = dict(data or {})
    payload["ok"] = False
    message = str(payload.get("error_message") or payload.get("error") or fallback_message).strip() or fallback_message
    payload["error"] = message
    payload["error_message"] = message
    payload["error_code"] = str(payload.get("error_code") or fallback_code)
    if suggestions and not payload.get("suggestions"):
        payload["suggestions"] = [s for s in suggestions if str(s).strip()]
    return payload


def _http_probe(url: str, timeout: int = 5, headers: Dict[str, str] | None = None) -> Tuple[bool, int | None, str]:
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or {})
        ok = resp.status_code < 500
        return ok, resp.status_code, ""
    except Exception as exc:
        return False, None, str(exc)


def _host_port_from_url(url: str, default_port: int) -> Tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        return host, parsed.port
    if parsed.scheme == "https":
        return host, 443
    return host, default_port


def _tcp_probe(host: str, port: int, timeout_s: float = 0.8) -> Tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True, ""
    except Exception as exc:
        return False, str(exc)


def _ensure_csrf() -> str:
    with _LOCK:
        resp = _SESSION.get(_django("/api/health/"), timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 500:
        raise RuntimeError(f"django health failed: {resp.status_code}")
    token = _SESSION.cookies.get("csrftoken", "")
    if token:
        return token
    return "local-api-csrf"


def _is_local_vllm_required() -> bool:
    provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    primary = (os.environ.get("LLM_PRIMARY_PROVIDER") or "").strip().lower()
    if provider in {"local_vllm", "local", "vllm"}:
        return True
    if primary in {"local_vllm", "local", "vllm"}:
        return True
    return _is_truthy(os.environ.get("LLM_REQUIRE_LOCAL_VLLM"))


def _resolve_local_model_path() -> Path:
    raw = (os.environ.get("LOCAL_VLLM_MODEL") or "./hf_models/Qwen3-8B-AWQ").strip().strip('"').strip("'")
    p = Path(raw)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def _load_current_version() -> str:
    env_ver = (os.environ.get("APP_CURRENT_VERSION") or "").strip()
    if env_ver:
        return env_ver
    if VERSION_FILE.exists():
        try:
            value = VERSION_FILE.read_text(encoding="utf-8", errors="ignore").strip()
            if value:
                return value
        except Exception:
            pass
    return "0.0.0"


def _parse_version(v: str) -> Tuple[int, int, int]:
    clean = (v or "0.0.0").strip().lower().lstrip("v")
    parts = clean.split(".")
    nums: List[int] = []
    for i in range(3):
        if i >= len(parts):
            nums.append(0)
            continue
        token = ""
        for ch in parts[i]:
            if ch.isdigit():
                token += ch
            else:
                break
        nums.append(int(token) if token else 0)
    return nums[0], nums[1], nums[2]


def _is_newer_version(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _resolve_redis_probe() -> Tuple[str, int]:
    broker = (os.environ.get("CELERY_BROKER_URL") or "redis://127.0.0.1:6379/0").strip()
    parsed = urlparse(broker)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    return host, port


def _runtime_dir_writable() -> Tuple[bool, str]:
    test_dir = PROJECT_ROOT / "runtime" / "logs"
    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        probe = test_dir / ".write_probe"
        probe.write_text(datetime.now().isoformat(), encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _build_preflight_report() -> Dict[str, Any]:
    django_ok, django_status, django_detail = _http_probe(_django("/api/health/"), timeout=5)
    worker_ok, worker_status, worker_detail = _http_probe(WORKER_HEALTH_URL, timeout=5)

    redis_host, redis_port = _resolve_redis_probe()
    redis_ok, redis_detail = _tcp_probe(redis_host, redis_port, timeout_s=0.8)

    vllm_required = _is_local_vllm_required()
    vllm_base = (os.environ.get("LOCAL_VLLM_BASE_URL") or "http://127.0.0.1:8002/v1").rstrip("/")
    vllm_host, vllm_port = _host_port_from_url(vllm_base, 8002)
    vllm_tcp_ok, vllm_tcp_detail = _tcp_probe(vllm_host, vllm_port)
    vllm_api_key = (os.environ.get("LOCAL_VLLM_API_KEY") or "").strip()
    vllm_headers = {"Authorization": f"Bearer {vllm_api_key}"} if vllm_api_key else None
    vllm_http_ok, vllm_http_status, vllm_http_detail = _http_probe(
        f"{vllm_base}/models", timeout=4, headers=vllm_headers
    )
    vllm_ok = vllm_tcp_ok and vllm_http_ok

    model_path = _resolve_local_model_path()
    model_exists = model_path.exists()

    runtime_ok, runtime_detail = _runtime_dir_writable()

    checks: Dict[str, Any] = {
        "django": {
            "ok": django_ok,
            "endpoint": _django("/api/health/"),
            "status_code": django_status,
            "detail": django_detail,
        },
        "worker": {
            "ok": worker_ok,
            "endpoint": WORKER_HEALTH_URL,
            "status_code": worker_status,
            "detail": worker_detail,
        },
        "redis": {
            "ok": redis_ok,
            "host": redis_host,
            "port": redis_port,
            "detail": redis_detail,
        },
        "vllm": {
            "required": vllm_required,
            "ok": vllm_ok if vllm_required else (vllm_ok or True),
            "endpoint": vllm_base,
            "tcp_ok": vllm_tcp_ok,
            "http_ok": vllm_http_ok,
            "http_status": vllm_http_status,
            "detail": vllm_http_detail or vllm_tcp_detail,
        },
        "model": {
            "required": vllm_required,
            "ok": model_exists if vllm_required else (model_exists or True),
            "path": str(model_path),
        },
        "runtime": {
            "ok": runtime_ok,
            "path": str((PROJECT_ROOT / "runtime" / "logs").resolve()),
            "detail": runtime_detail,
        },
    }

    suggestions: list[str] = []
    error_code = ""
    error_message = ""

    if not django_ok:
        error_code = "E-UPSTREAM-DJANGO-UNREACHABLE"
        error_message = "业务服务尚未启动，请先启动系统服务。"
        suggestions.append("请在项目根目录执行 start_all.bat start，等待 5-10 秒后重试。")
    elif not worker_ok:
        error_code = "E-UPSTREAM-WORKER-UNREACHABLE"
        error_message = "审查引擎未就绪，暂时无法处理合同。"
        suggestions.append("请执行 start_all.bat status 检查 Worker 状态。")
    elif not redis_ok:
        error_code = "E-UPSTREAM-REDIS-UNREACHABLE"
        error_message = "任务队列服务未就绪，审查任务无法排队。"
        suggestions.append(f"请确认 Redis 已启动并可访问：{redis_host}:{redis_port}")
    elif vllm_required and not vllm_ok:
        error_code = "E-UPSTREAM-VLLM-UNREACHABLE"
        error_message = "本地模型服务未就绪，无法开始智能审查。"
        suggestions.append("请确认 LOCAL_VLLM_BASE_URL 可访问，或检查 vLLM 进程是否启动。")
    elif vllm_required and not model_exists:
        error_code = "E-MODEL-PATH-NOT-FOUND"
        error_message = "未找到本地模型目录，请检查模型路径配置。"
        suggestions.append(f"请确认模型目录存在：{model_path}")
    elif not runtime_ok:
        error_code = "E-RUNTIME-NOT-WRITABLE"
        error_message = "运行目录不可写，日志与结果可能无法保存。"
        suggestions.append("请检查项目目录写入权限，或切换到有写权限的目录运行。")

    ready = not error_code
    summary = "启动检查通过" if ready else error_message
    if not ready and not suggestions:
        suggestions.append("请先执行 start_all.bat status 观察各服务状态。")

    return {
        "ok": ready,
        "service": "local_api",
        "summary": summary,
        "error_code": error_code,
        "error_message": error_message,
        "suggestions": suggestions,
        "checks": checks,
        "current_version": _load_current_version(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def _proxy_get_json(path: str) -> JSONResponse:
    try:
        with _LOCK:
            resp = _SESSION.get(_django(path), timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        return _error_response(
            status_code=502,
            code="E-PROXY-UPSTREAM-FAILED",
            message="服务连接失败，请确认后端已启动。",
            detail=str(exc),
            suggestions=["请执行 start_all.bat start，然后重试。"],
        )

    data = _safe_json(resp)
    if resp.status_code >= 400:
        payload = _normalize_upstream_error(
            data,
            fallback_code="E-UPSTREAM-REQUEST-FAILED",
            fallback_message="上游服务返回异常",
        )
        return JSONResponse(status_code=resp.status_code, content=payload)
    return JSONResponse(status_code=resp.status_code, content=data)


def _load_update_manifest() -> Dict[str, Any]:
    if not UPDATE_MANIFEST_URL:
        return {}
    resp = requests.get(UPDATE_MANIFEST_URL, timeout=8)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("manifest format invalid")
    return payload


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "local_api"}


@app.get("/contract/api/health/")
def contract_health():
    report = _build_preflight_report()
    payload = {
        "ok": report["ok"],
        "service": "local_api",
        "summary": report["summary"],
        "error_code": report["error_code"],
        "error_message": report["error_message"],
        "suggestions": report["suggestions"],
        "upstream": {
            "django": report["checks"]["django"]["ok"],
            "worker": report["checks"]["worker"]["ok"],
            "redis": report["checks"]["redis"]["ok"],
        },
    }
    resp = JSONResponse(status_code=200 if report["ok"] else 503, content=payload)
    resp.set_cookie("csrftoken", "local-api-csrf")
    return resp


@app.get("/contract/api/preflight/")
def contract_preflight():
    report = _build_preflight_report()
    return JSONResponse(status_code=200 if report["ok"] else 503, content=report)


@app.get("/contract/api/update/check/")
def contract_update_check():
    current = _load_current_version()
    if not UPDATE_MANIFEST_URL:
        return {
            "ok": True,
            "current_version": current,
            "latest_version": current,
            "has_update": False,
            "manifest_url": "",
            "download_url": "",
            "sha256": "",
            "notes": "",
            "published_at": "",
            "message": "未配置更新清单地址（APP_UPDATE_MANIFEST_URL）。",
        }

    try:
        manifest = _load_update_manifest()
        latest = str(manifest.get("version") or current)
        download_url = str(manifest.get("download_url") or "")
        sha256 = str(manifest.get("sha256") or "")
        notes = str(manifest.get("notes") or "")
        published_at = str(manifest.get("published_at") or "")
        has_update = _is_newer_version(latest, current)
        return {
            "ok": True,
            "current_version": current,
            "latest_version": latest,
            "has_update": has_update,
            "manifest_url": UPDATE_MANIFEST_URL,
            "download_url": download_url,
            "sha256": sha256,
            "notes": notes,
            "published_at": published_at,
            "message": "发现可用更新。" if has_update else "当前已是最新版本。",
        }
    except Exception as exc:
        return _error_response(
            status_code=502,
            code="E-UPDATE-MANIFEST-FAILED",
            message="检查更新失败，请稍后重试。",
            detail=str(exc),
            suggestions=["请检查 APP_UPDATE_MANIFEST_URL 是否可访问。"],
        )


@app.post("/contract/api/start/")
async def contract_start(file: UploadFile = File(...)):
    filename = (file.filename or "uploaded.pdf").strip()
    if not filename.lower().endswith(".pdf"):
        return _error_response(
            status_code=400,
            code="E-FILE-TYPE-PDF-ONLY",
            message="仅支持上传 PDF 文件。",
            suggestions=["请确认文件扩展名为 .pdf，且文件内容为有效 PDF。"],
        )

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
    except Exception as exc:
        return _error_response(
            status_code=502,
            code="E-PROXY-START-FAILED",
            message="任务提交失败，请确认后端服务可用。",
            detail=str(exc),
            suggestions=["请执行 start_all.bat status 检查服务状态。"],
        )

    data = _safe_json(resp)
    if resp.status_code >= 400:
        payload = _normalize_upstream_error(
            data,
            fallback_code="E-UPSTREAM-START-FAILED",
            fallback_message="上游服务拒绝了任务提交",
        )
        return JSONResponse(status_code=resp.status_code, content=payload)
    return JSONResponse(status_code=resp.status_code, content=data)


@app.get("/contract/api/status/{job_id}/")
def contract_status(job_id: int):
    return _proxy_get_json(f"/api/status/{job_id}/")


@app.get("/contract/api/result/{job_id}/")
def contract_result(job_id: int):
    return _proxy_get_json(f"/api/result/{job_id}/")


@app.get("/contract/api/export_pdf/{job_id}/")
def contract_export_pdf(job_id: int):
    try:
        with _LOCK:
            resp = _SESSION.get(_django(f"/api/export_pdf/{job_id}/"), timeout=max(REQUEST_TIMEOUT, 120))
    except Exception as exc:
        return _error_response(
            status_code=502,
            code="E-PROXY-EXPORT-PDF-FAILED",
            message="报告导出失败，请稍后重试。",
            detail=str(exc),
            suggestions=["请先确认任务已完成，再执行导出。"],
        )

    if resp.status_code != 200:
        data = _safe_json(resp)
        payload = _normalize_upstream_error(
            data,
            fallback_code="E-UPSTREAM-EXPORT-PDF-FAILED",
            fallback_message="上游服务导出 PDF 失败",
        )
        return JSONResponse(status_code=resp.status_code, content=payload)
    return Response(content=resp.content, media_type="application/pdf")


@app.get("/contract/api/export_logs/")
def contract_export_logs():
    try:
        report = _build_preflight_report()
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "diagnostics/preflight.json",
                json.dumps(report, ensure_ascii=False, indent=2),
            )
            env_summary = {
                "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", ""),
                "LLM_PRIMARY_PROVIDER": os.environ.get("LLM_PRIMARY_PROVIDER", ""),
                "LLM_REQUIRE_LOCAL_VLLM": os.environ.get("LLM_REQUIRE_LOCAL_VLLM", ""),
                "LOCAL_VLLM_BASE_URL": os.environ.get("LOCAL_VLLM_BASE_URL", ""),
                "LOCAL_VLLM_MODEL": os.environ.get("LOCAL_VLLM_MODEL", ""),
                "APP_UPDATE_MANIFEST_URL": UPDATE_MANIFEST_URL,
                "APP_CURRENT_VERSION": _load_current_version(),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            zf.writestr(
                "diagnostics/env_summary.json",
                json.dumps(env_summary, ensure_ascii=False, indent=2),
            )

            candidates: list[Path] = []
            for folder in [PROJECT_ROOT / "runtime" / "logs", PROJECT_ROOT / "runtime"]:
                if folder.exists() and folder.is_dir():
                    for p in folder.rglob("*"):
                        if p.is_file():
                            candidates.append(p)

            candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
            for p in candidates[:40]:
                try:
                    if p.stat().st_size > 5 * 1024 * 1024:
                        continue
                    arc = p.relative_to(PROJECT_ROOT).as_posix()
                    zf.write(p, f"diagnostics/{arc}")
                except Exception:
                    continue

        data = mem.getvalue()
        filename = f"contract_review_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return _error_response(
            status_code=500,
            code="E-EXPORT-LOGS-FAILED",
            message="诊断包导出失败，请稍后重试。",
            detail=str(exc),
        )


