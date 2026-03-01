import hashlib
import io
import json
import os
import re
import shutil
import time
from html import escape
from pathlib import Path
from typing import Optional

import pdfkit
import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from jinja2 import Template
from packages.core_engine.result_contract import STAMP_TEXT_KEY, stamp_status_to_cn
from packages.shared_contract_schema import (
    build_report_html as build_shared_report_html,
    build_report_markdown as build_shared_report_markdown,
    build_report_payload as build_shared_report_payload,
    normalize_result_json,
)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

from .models import ContractJob
from .services.stamp_detect import detect_stamp_status

_WORKER_SESSION = requests.Session()


def _get_worker_base_url() -> str:
    return getattr(settings, "WORKER_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def _get_worker_timeout() -> int:
    # /analyze 是提交任务，不应阻塞太久
    return int(getattr(settings, "WORKER_TIMEOUT", 30))


def _get_worker_submit_retry() -> int:
    return int(getattr(settings, "WORKER_SUBMIT_RETRY", 1))


def _require_worker_token(request) -> bool:
    token = (getattr(settings, "WORKER_TOKEN", "") or os.environ.get("WORKER_TOKEN", "")).strip()
    if not token:
        return True
    req_token = request.headers.get("X-Worker-Token") or request.META.get("HTTP_X_WORKER_TOKEN")
    return bool(req_token) and req_token == token


def _merge_runtime_meta(current: Optional[dict], incoming: Optional[dict], *, stage: str = "", progress: Optional[int] = None) -> dict:
    meta = dict(current) if isinstance(current, dict) else {}

    if isinstance(incoming, dict):
        for k, v in incoming.items():
            meta[k] = v

    if stage:
        history = meta.get("stage_history")
        if not isinstance(history, list):
            history = []

        event = {
            "stage": stage,
            "ts": timezone.now().isoformat(timespec="seconds"),
        }
        if progress is not None:
            event["progress"] = progress

        should_append = True
        if history:
            last = history[-1]
            if isinstance(last, dict) and last.get("stage") == stage and last.get("progress") == progress:
                should_append = False

        if should_append:
            history.append(event)
            if len(history) > 120:
                history = history[-120:]
        meta["stage_history"] = history

    meta["updated_at"] = timezone.now().isoformat(timespec="seconds")
    return meta


@require_http_methods(["GET"])
@ensure_csrf_cookie
def api_health(request):
    return JsonResponse({"ok": True, "service": "django"})


@require_http_methods(["POST"])
def start_analyze(request):
    """
    Upload PDF -> save to MEDIA_ROOT/job_{id}_{sha}/input.pdf
    -> create ContractJob
    -> submit worker task: POST {WORKER_BASE_URL}/analyze
    """
    upload = request.FILES.get("file")
    if not upload:
        return HttpResponseBadRequest("missing file (form-data key should be 'file')")

    filename = getattr(upload, "name", "uploaded.pdf")
    if not filename.lower().endswith(".pdf"):
        return HttpResponseBadRequest("only PDF is supported")

    content_type_raw = (upload.content_type or "").lower().strip()
    content_type = content_type_raw.split(";", 1)[0].strip()
    allowed_types = {
        "",
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
        "binary/octet-stream",
    }
    if content_type not in allowed_types:
        return HttpResponseBadRequest("invalid file type (expected PDF)")

    # Some desktop pickers send octet-stream for valid PDFs; verify PDF magic header when readable.
    try:
        pos = upload.tell() if hasattr(upload, "tell") else None
        head = upload.read(5) or b""
        if hasattr(upload, "seek"):
            upload.seek(pos if pos is not None else 0)
        if head and not bytes(head).startswith(b"%PDF-"):
            return HttpResponseBadRequest("invalid file content (expected PDF)")
    except Exception:
        # Keep compatibility for non-seekable upload streams.
        pass

    job = ContractJob.objects.create(
        status="queued",
        progress=0,
        stage="queued",
        file_sha256="",
        filename=filename,
        result_markdown="",
        runtime_meta={"stage_history": [{"stage": "queued", "progress": 0, "ts": timezone.now().isoformat(timespec="seconds")}]},
        error="",
    )

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
    media_root.mkdir(parents=True, exist_ok=True)

    tmp_root = media_root / f"job_{job.id}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    pdf_path = tmp_root / "input.pdf"

    h = hashlib.sha256()
    with open(pdf_path, "wb") as f:
        for chunk in upload.chunks():
            h.update(chunk)
            f.write(chunk)
    file_sha256 = h.hexdigest()

    final_root = media_root / f"job_{job.id}_{file_sha256}"
    if final_root.exists():
        shutil.rmtree(final_root)
    tmp_root.rename(final_root)

    job.file_sha256 = file_sha256
    job.save(update_fields=["file_sha256"])

    worker_url = _get_worker_base_url() + "/analyze"
    payload = {
        "job_id": job.id,
        "pdf_path": str((final_root / "input.pdf").resolve()),
        "out_root": str(final_root.resolve()),
    }

    try:
        data = {}
        last_err = None
        submit_attempts = 0
        submit_started = time.perf_counter()

        for _ in range(max(1, _get_worker_submit_retry())):
            submit_attempts += 1
            try:
                r = _WORKER_SESSION.post(worker_url, json=payload, timeout=_get_worker_timeout())
                r.raise_for_status()
                try:
                    data = r.json()
                except Exception:
                    data = {}
                last_err = None
                break
            except Exception as e:
                last_err = e

        if last_err is not None:
            raise last_err

        if isinstance(data, dict) and data.get("ok") is False:
            raise RuntimeError(f"worker returned ok=false: {data.get('error')}")

        submit_seconds = round(time.perf_counter() - submit_started, 3)
        runtime_meta = _merge_runtime_meta(
            job.runtime_meta,
            {"submit_attempts": submit_attempts, "submit_seconds": submit_seconds},
            stage="submitted",
            progress=1,
        )

        job.status = "running"
        job.stage = "submitted"
        job.progress = 1
        job.runtime_meta = runtime_meta
        job.save(update_fields=["status", "stage", "progress", "runtime_meta"])

    except Exception as e:
        runtime_meta = _merge_runtime_meta(
            job.runtime_meta,
            {"submit_failed": True},
            stage="submit_failed",
            progress=100,
        )
        job.status = "error"
        job.error = f"submit to worker failed: {e}"
        job.runtime_meta = runtime_meta
        job.save(update_fields=["status", "error", "runtime_meta"])
        return JsonResponse({"ok": False, "error": job.error}, status=500)

    return JsonResponse({"ok": True, "job_id": job.id})



@require_http_methods(["GET"])
def job_status(request, job_id: int):
    row = ContractJob.objects.filter(id=job_id).values(
        "id",
        "status",
        "progress",
        "stage",
        "result_json",
        "filename",
        "result_markdown",
        "error",
        "runtime_meta",
    ).first()
    if not row:
        return JsonResponse({"ok": False, "error": "job not found"}, status=404)

    status = row.get("status") or "queued"
    include_result = status in {"done", "error"}
    result_json = normalize_result_json(row.get("result_json")) if include_result else None
    result_markdown = row.get("result_markdown") if status == "done" else ""

    report_payload = None
    report_html = ""
    if status == "done":
        report_payload = build_shared_report_payload(result_json, result_markdown or "")
        report_html = build_shared_report_html(report_payload)

    return JsonResponse(
        {
            "ok": True,
            "job_id": row["id"],
            "status": status,
            "progress": row.get("progress") or 0,
            "stage": row.get("stage") or "",
            "result_json": result_json,
            "filename": row.get("filename") or "",
            "result_markdown": result_markdown or "",
            "report_payload": report_payload,
            "report_html": report_html,
            "runtime_meta": row.get("runtime_meta") if isinstance(row.get("runtime_meta"), dict) else {},
            "error": row.get("error") if status == "error" else "",
        }
    )


@require_http_methods(["GET"])
def job_result(request, job_id: int):
    row = ContractJob.objects.filter(id=job_id).values(
        "id",
        "status",
        "progress",
        "stage",
        "result_json",
        "result_markdown",
        "filename",
        "error",
        "runtime_meta",
    ).first()
    if not row:
        return JsonResponse({"ok": False, "error": "job not found"}, status=404)

    status = row.get("status") or "queued"
    is_ready = status in {"done", "error"}

    if is_ready:
        result_json = normalize_result_json(row.get("result_json"))
        result_markdown = row.get("result_markdown") or ""
        report_payload = build_shared_report_payload(result_json, result_markdown)
        report_html = build_shared_report_html(report_payload)
        report_markdown = build_shared_report_markdown(report_payload)
    else:
        result_json = None
        result_markdown = ""
        report_payload = None
        report_html = ""
        report_markdown = ""

    return JsonResponse(
        {
            "ok": True,
            "job_id": row["id"],
            "ready": is_ready,
            "status": status,
            "progress": row.get("progress") or 0,
            "stage": row.get("stage") or "",
            "filename": row.get("filename") or "",
            "runtime_meta": row.get("runtime_meta") if isinstance(row.get("runtime_meta"), dict) else {},
            "error": row.get("error") if status == "error" else "",
            "result_json": result_json,
            "result_markdown": result_markdown,
            "report_payload": report_payload,
            "report_html": report_html,
            "report_markdown": report_markdown,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def job_update(request):
    """
    Worker callback endpoint: /contract/api/job/update/
    POST JSON: {job_id,status,progress,stage,result_markdown,result_json,error}
    Target: avoid 500; if internal failure happens, mark job as error.
    """
    try:
        if not _require_worker_token(request):
            return JsonResponse({"ok": False, "error": "unauthorized worker"}, status=403)

        payload = json.loads(request.body.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            return JsonResponse({"ok": False, "error": "payload must be json object"}, status=400)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    job_id = payload.get("job_id")
    if job_id is None or job_id == "":
        return JsonResponse({"ok": False, "error": "missing job_id"}, status=400)

    try:
        job_id_int = int(job_id)
    except Exception:
        return JsonResponse({"ok": False, "error": f"invalid job_id: {job_id}"}, status=400)

    try:
        job = ContractJob.objects.get(id=job_id_int)
    except ContractJob.DoesNotExist:
        return JsonResponse({"ok": False, "error": "job not found"}, status=404)

    try:
        update_fields = []

        status_val = payload.get("status")
        if status_val and status_val != job.status:
            job.status = status_val
            update_fields.append("status")

        if payload.get("stage") is not None:
            stage_val = payload.get("stage") or job.stage
            if stage_val != job.stage:
                job.stage = stage_val
                update_fields.append("stage")

        if payload.get("progress") is not None:
            try:
                p = int(payload.get("progress"))
                if p != job.progress:
                    job.progress = p
                    update_fields.append("progress")
            except Exception:
                pass

        cur = job.result_json if isinstance(job.result_json, dict) else {}
        result_json_changed = False
        runtime_meta = job.runtime_meta if isinstance(job.runtime_meta, dict) else {}

        if payload.get("result_markdown") is not None:
            md = payload.get("result_markdown", "") or ""
            max_chars = int(getattr(settings, "MAX_RESULT_MARKDOWN_CHARS", 200000) or 0)
            if max_chars > 0 and len(md) > max_chars:
                md = md[:max_chars]
                cur["result_markdown_truncated"] = True
                result_json_changed = True
            if md != job.result_markdown:
                job.result_markdown = md
                update_fields.append("result_markdown")

        if payload.get("error") is not None:
            err_val = payload.get("error", "") or ""
            if err_val != job.error:
                job.error = err_val
                update_fields.append("error")

        incoming = payload.get("result_json", None)
        if incoming is not None:
            if isinstance(incoming, dict):
                cur.update(incoming)
            else:
                cur["result_json_raw"] = incoming
            result_json_changed = True
        if isinstance(cur, dict) and "是否盖章" in cur and STAMP_TEXT_KEY not in cur:
            cur[STAMP_TEXT_KEY] = cur.get("是否盖章")
            result_json_changed = True

        has_stamp = isinstance(cur, dict) and ("stamp_status" in cur or STAMP_TEXT_KEY in cur or "是否盖章" in cur)
        should_attempt_stamp = payload.get("result_markdown") is not None or status_val in {"done", "error"}
        if (not has_stamp) and should_attempt_stamp:
            text_for_stamp = payload.get("result_markdown") if payload.get("result_markdown") is not None else (job.result_markdown or "")
            try:
                stamp = detect_stamp_status(text_for_stamp)
                cur[STAMP_TEXT_KEY] = stamp_status_to_cn(stamp.get("stamp_status"))
                cur["stamp_status"] = stamp.get("stamp_status")
                cur["stamp_evidence"] = stamp.get("evidence")
            except Exception as e:
                cur["stamp_status"] = "ERROR"
                cur["stamp_error"] = str(e)
            result_json_changed = True

        if result_json_changed:
            job.result_json = normalize_result_json(cur)
            update_fields.append("result_json")

        incoming_meta = payload.get("meta")
        merged_meta = _merge_runtime_meta(
            runtime_meta,
            incoming_meta if isinstance(incoming_meta, dict) else None,
            stage=job.stage or (payload.get("stage") or ""),
            progress=job.progress,
        )
        if merged_meta != runtime_meta:
            job.runtime_meta = merged_meta
            update_fields.append("runtime_meta")

        if update_fields:
            job.save(update_fields=sorted(set(update_fields)))

        return JsonResponse({"ok": True})

    except Exception as e:
        try:
            job.status = "error"
            job.progress = 100
            job.error = f"job_update failed: {e}"
            job.save(update_fields=["status", "progress", "error"])
        except Exception:
            pass
        return JsonResponse({"ok": False, "error": str(e)}, status=200)


PDF_HTML_TEMPLATE = Template(
    """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <style>
    body { font-family: "Microsoft YaHei","PingFang SC","Hiragino Sans GB",Arial,sans-serif; }
    h1 { font-size: 22px; margin: 0 0 12px; }
    h2 { font-size: 16px; margin: 18px 0 8px; }
    pre { white-space: pre-wrap; }
  </style>
</head>
<body>
  {{ body|safe }}
</body>
</html>
"""
)


def _resolve_wkhtmltopdf_path() -> str:
    candidates: list[str] = []
    configured = (getattr(settings, "WKHTMLTOPDF_BIN", "") or os.environ.get("WKHTMLTOPDF_BIN", "")).strip().strip('"').strip("'")
    if configured:
        candidates.append(configured)

    which_path = shutil.which("wkhtmltopdf") or shutil.which("wkhtmltopdf.exe")
    if which_path:
        candidates.append(which_path)

    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    candidates.extend(
        [
            str(base_dir / "wkhtmltopdf" / "bin" / "wkhtmltopdf.exe"),
            str(base_dir / "wkhtmltopdf.exe"),
            str(base_dir / "tools" / "wkhtmltopdf" / "bin" / "wkhtmltopdf.exe"),
            str(base_dir / ".venv" / "wkhtmltopdf" / "bin" / "wkhtmltopdf.exe"),
            r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
            r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
        ]
    )

    seen: set[str] = set()
    for raw in candidates:
        path = os.path.expandvars(str(raw)).strip().strip('"').strip("'")
        if not path:
            continue
        normalized = str(Path(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.exists(normalized):
            return normalized
    return ""


def _register_reportlab_font() -> str:
    if not REPORTLAB_AVAILABLE:
        return "Helvetica"

    font_candidates = [
        ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc"),
        ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
        ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
        ("ArialUnicodeMS", r"C:\Windows\Fonts\arialuni.ttf"),
    ]

    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, font_path in font_candidates:
        if name in registered:
            return name
        if not os.path.exists(font_path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, font_path))
            return name
        except Exception:
            continue
    return "Helvetica"


def _risk_level_color(level: str) -> str:
    text = (level or "").lower()
    if any(k in text for k in ("高", "high", "严重", "critical")):
        return "#b42318"
    if any(k in text for k in ("中", "medium", "moderate")):
        return "#b54708"
    if any(k in text for k in ("低", "low")):
        return "#067647"
    return "#475467"


def _build_panel_table(title: str, body_flowables: list, width: float, styles: dict) -> Table:
    inner = [Paragraph(escape(title), styles["panel_title"]), Spacer(1, 4)]
    if body_flowables:
        inner.extend(body_flowables)
    else:
        inner.append(Paragraph("未识别到相关内容", styles["muted"]))
    panel = Table([[inner]], colWidths=[width])
    panel.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#d6e3e0")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return panel


def _build_pdf_with_reportlab(report_payload: dict, title: str) -> bytes:
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("reportlab is not available")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
        author="Contract Review",
    )
    font_name = _register_reportlab_font()
    base = getSampleStyleSheet()["BodyText"]
    styles = {
        "title": ParagraphStyle(
            "title",
            parent=base,
            fontName=font_name,
            fontSize=22 if font_name != "Helvetica" else 18,
            leading=28,
            textColor=colors.HexColor("#4b6fae"),
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "meta",
            parent=base,
            fontName=font_name,
            fontSize=10.5 if font_name != "Helvetica" else 9.5,
            leading=15,
            textColor=colors.HexColor("#475467"),
            spaceAfter=3,
        ),
        "panel_title": ParagraphStyle(
            "panel_title",
            parent=base,
            fontName=font_name,
            fontSize=13 if font_name != "Helvetica" else 11.5,
            leading=17,
            textColor=colors.HexColor("#2f4b6e"),
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base,
            fontName=font_name,
            fontSize=10.5 if font_name != "Helvetica" else 9.5,
            leading=16,
            textColor=colors.HexColor("#101828"),
        ),
        "body_muted": ParagraphStyle(
            "body_muted",
            parent=base,
            fontName=font_name,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#667085"),
        ),
        "key": ParagraphStyle(
            "key",
            parent=base,
            fontName=font_name,
            fontSize=10.2 if font_name != "Helvetica" else 9.2,
            leading=15,
            textColor=colors.HexColor("#475467"),
        ),
        "item": ParagraphStyle(
            "item",
            parent=base,
            fontName=font_name,
            fontSize=10.5 if font_name != "Helvetica" else 9.5,
            leading=16,
            textColor=colors.HexColor("#101828"),
            spaceAfter=3,
        ),
        "muted": ParagraphStyle(
            "muted",
            parent=base,
            fontName=font_name,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#667085"),
        ),
    }

    story = []
    story.append(Paragraph("合同审查报告", styles["title"]))
    stamp_color = report_payload.get("stamp_color", "#6b7280")
    meta_line_1 = (
        f"<b>合同类型：</b>{escape(str(report_payload.get('contract_type', '未识别')))}"
        f"  |  <b>盖章：</b><font color='{stamp_color}'><b>{escape(str(report_payload.get('stamp_text', '未提及')))}</b></font>"
    )
    meta_line_2 = (
        f"<b>类型置信度：</b>{escape(str(report_payload.get('confidence_text', '-')))}"
        f"  |  <b>类型来源：</b>{escape(str(report_payload.get('type_source', 'result_json')))}"
    )
    story.append(Paragraph(meta_line_1, styles["meta"]))
    story.append(Paragraph(meta_line_2, styles["meta"]))
    story.append(Spacer(1, 4))

    key_rows = []
    for k, v in (report_payload.get("key_facts") or {}).items():
        key_rows.append(
            [
                Paragraph(f"<b>{escape(str(k))}</b>", styles["key"]),
                Paragraph(escape(str(v)), styles["body"]),
            ]
        )
    if not key_rows:
        key_rows = [[Paragraph("<b>提示</b>", styles["key"]), Paragraph("未提取到关键要素", styles["body_muted"])]]
    key_table = Table(key_rows, colWidths=[30 * mm, doc.width - (30 * mm)])
    key_table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.HexColor("#edf2f7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(_build_panel_table("合同关键要素", [key_table], doc.width, styles))
    story.append(Spacer(1, 6))

    overview = escape(str(report_payload.get("overview", "暂无概述")))
    story.append(_build_panel_table("审查概述", [Paragraph(overview, styles["body"])], doc.width, styles))
    story.append(Spacer(1, 6))

    def _append_items_section(title: str, items: list) -> None:
        section_header = Table([[Paragraph(escape(title), styles["panel_title"])]], colWidths=[doc.width])
        section_header.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#d6e3e0")),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(section_header)
        story.append(Spacer(1, 4))
        if not items:
            story.append(Paragraph("未识别到相关内容", styles["muted"]))
            story.append(Spacer(1, 6))
            return

        for idx, item in enumerate(items, 1):
            name = escape(str(item.get("title", "事项")))
            level = str(item.get("level", "") or "")
            level_html = ""
            if level:
                level_html = f" <font color='{_risk_level_color(level)}'>（{escape(level)}）</font>"
            story.append(Paragraph(f"<b>{idx}. {name}</b>{level_html}", styles["item"]))
            problem = str(item.get("problem", "") or "")
            if problem:
                story.append(Paragraph(f"<b>问题：</b>{escape(problem)}", styles["body"]))
            suggestion = str(item.get("suggestion", "") or "")
            if suggestion:
                story.append(Paragraph(f"{escape(suggestion)}", styles["body"]))
            story.append(Spacer(1, 3))
        story.append(Spacer(1, 6))

    _append_items_section("风险点", report_payload.get("risks", []))
    _append_items_section("改进建议", report_payload.get("improvements", []))

    doc.build(story)
    return buf.getvalue()


@require_http_methods(["GET"])
def export_pdf(request, job_id: int):
    try:
        job = ContractJob.objects.get(id=job_id)
    except ContractJob.DoesNotExist:
        return JsonResponse({"ok": False, "error": "job not found"}, status=404)

    result_json = normalize_result_json(job.result_json)
    report_payload = build_shared_report_payload(result_json, job.result_markdown or "")
    report_markdown = build_shared_report_markdown(report_payload)
    if not report_markdown:
        return JsonResponse({"ok": False, "error": "job has no report content yet"}, status=400)

    html = PDF_HTML_TEMPLATE.render(body=build_shared_report_html(report_payload))

    options = {
        "encoding": "UTF-8",
        "page-size": "A4",
        "margin-top": "12mm",
        "margin-right": "12mm",
        "margin-bottom": "12mm",
        "margin-left": "12mm",
        "enable-local-file-access": None,
        "disable-smart-shrinking": None,
    }

    wk_path = _resolve_wkhtmltopdf_path()
    wk_error = ""
    if wk_path:
        try:
            config = pdfkit.configuration(wkhtmltopdf=wk_path)
            pdf_bytes = pdfkit.from_string(html, False, options=options, configuration=config, verbose=False)
            resp = HttpResponse(pdf_bytes, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="contract_review_job_{job_id}.pdf"'
            resp["X-PDF-Engine"] = "wkhtmltopdf"
            return resp
        except Exception as e:
            wk_error = str(e)
    else:
        wk_error = f"WKHTMLTOPDF_BIN not found. configured={getattr(settings, 'WKHTMLTOPDF_BIN', '')}"

    try:
        fallback_bytes = _build_pdf_with_reportlab(report_payload, f"合同审查报告 #{job_id}")
        resp = HttpResponse(fallback_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="contract_review_job_{job_id}.pdf"'
        resp["X-PDF-Engine"] = "reportlab-fallback"
        return resp
    except Exception as fallback_err:
        return JsonResponse(
            {
                "ok": False,
                "error": f"pdf export failed; wkhtmltopdf={wk_error}; reportlab={fallback_err}",
            },
            status=500,
        )
