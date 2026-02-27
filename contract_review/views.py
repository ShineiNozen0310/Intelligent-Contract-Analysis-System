import hashlib
import io
import json
import os
import re
import shutil
from html import escape
from pathlib import Path
from typing import Optional

import pdfkit
import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from jinja2 import Template

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
    if upload.content_type and upload.content_type not in {"application/pdf", "application/x-pdf"}:
        return HttpResponseBadRequest("invalid file type (expected PDF)")

    job = ContractJob.objects.create(
        status="queued",
        progress=0,
        stage="queued",
        file_sha256="",
        filename=filename,
        result_markdown="",
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
        for _ in range(max(1, _get_worker_submit_retry())):
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

        job.status = "running"
        job.stage = "submitted"
        job.progress = 1
        job.save(update_fields=["status", "stage", "progress"])

    except Exception as e:
        job.status = "error"
        job.error = f"submit to worker failed: {e}"
        job.save(update_fields=["status", "error"])
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
    ).first()
    if not row:
        return JsonResponse({"ok": False, "error": "job not found"}, status=404)

    status = row.get("status") or "queued"
    include_result = status in {"done", "error"}

    return JsonResponse(
        {
            "ok": True,
            "job_id": row["id"],
            "status": status,
            "progress": row.get("progress") or 0,
            "stage": row.get("stage") or "",
            "result_json": row.get("result_json") if include_result else None,
            "filename": row.get("filename") or "",
            "result_markdown": row.get("result_markdown") if status == "done" else "",
            "error": row.get("error") if status == "error" else "",
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

        has_stamp = isinstance(cur, dict) and ("stamp_status" in cur or "是否盖章" in cur)
        should_attempt_stamp = payload.get("result_markdown") is not None or status_val in {"done", "error"}
        if (not has_stamp) and should_attempt_stamp:
            text_for_stamp = payload.get("result_markdown") if payload.get("result_markdown") is not None else (job.result_markdown or "")
            try:
                stamp = detect_stamp_status(text_for_stamp)
                cur["是否盖章"] = (
                    "是" if stamp.get("stamp_status") == "YES"
                    else ("不确定" if stamp.get("stamp_status") == "UNCERTAIN" else "否")
                )
                cur["stamp_status"] = stamp.get("stamp_status")
                cur["stamp_evidence"] = stamp.get("evidence")
            except Exception as e:
                cur["stamp_status"] = "ERROR"
                cur["stamp_error"] = str(e)
            result_json_changed = True

        if result_json_changed:
            job.result_json = cur
            update_fields.append("result_json")

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


def _first_non_empty(values: list) -> str:
    for v in values:
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s
            continue
        if v is not None:
            return str(v)
    return ""


def _first_non_empty_value(values: list):
    for v in values:
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s
            continue
        if v is not None:
            return v
    return None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if x is not None]
    return [value]


def _extract_review_items(result_json: dict, keys: list[str]) -> list:
    cands = []
    for k in keys:
        cands.append(result_json.get(k))
    for prefix in ("result", "review", "data"):
        node = result_json.get(prefix)
        if isinstance(node, dict):
            for k in keys:
                cands.append(node.get(k))
    picked = _first_non_empty_value(cands)
    return _as_list(picked)


def _extract_improvement_suggestions_from_markdown(markdown_text: str, max_items: int = 8) -> list[dict]:
    if not markdown_text:
        return []
    out: list[dict] = []
    seen = set()
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip().lstrip("-*•").strip()
        if not line:
            continue
        m = re.search(r"(?:改进建议|优化建议|完善建议|修改建议|建议)[:：]\s*(.+)", line)
        if not m:
            continue
        suggestion = (m.group(1) or "").strip("；;。 ")
        if len(suggestion) < 6 or suggestion in seen:
            continue
        seen.add(suggestion)
        out.append({"title": "改进建议", "level": "", "problem": "", "suggestion": suggestion})
        if len(out) >= max_items:
            break
    return out


def _fallback_improvements_from_risks(risks: list[dict], max_items: int = 8) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for item in risks:
        if not isinstance(item, dict):
            continue
        suggestion = _first_non_empty(
            [item.get("suggestion"), item.get("advice"), item.get("fix"), item.get("solution"), item.get("建议"), item.get("修改建议")]
        )
        if not suggestion:
            continue
        suggestion = suggestion.strip()
        if len(suggestion) < 6 or suggestion in seen:
            continue
        seen.add(suggestion)
        out.append(
            {
                "title": _first_non_empty([item.get("title"), item.get("name"), item.get("item"), "改进建议"]),
                "level": "",
                "problem": _first_non_empty([item.get("problem"), item.get("issue"), item.get("description"), item.get("问题")]),
                "suggestion": suggestion,
            }
        )
        if len(out) >= max_items:
            break
    return out


def _has_meaningful_review_items(items: list[dict]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _first_non_empty([item.get("title"), item.get("name"), item.get("item"), item.get("风险点"), item.get("问题点")])
        problem = _first_non_empty([item.get("problem"), item.get("issue"), item.get("desc"), item.get("description"), item.get("问题")])
        suggestion = _first_non_empty([item.get("suggestion"), item.get("advice"), item.get("fix"), item.get("solution"), item.get("建议"), item.get("修改建议")])
        if title or problem or suggestion:
            return True
    return False


def _normalize_review_item(item: object, default_title: str) -> dict:
    if isinstance(item, str):
        text = item.strip()
        return {"title": text or default_title, "level": "", "problem": text, "suggestion": ""}
    if isinstance(item, dict):
        return {
            "title": _first_non_empty([item.get("title"), item.get("name"), item.get("item"), item.get("风险点"), item.get("问题点"), default_title]),
            "level": _first_non_empty([item.get("level"), item.get("severity"), item.get("risk_level"), item.get("风险等级")]),
            "problem": _first_non_empty([item.get("problem"), item.get("issue"), item.get("desc"), item.get("description"), item.get("问题")]),
            "suggestion": _first_non_empty(
                [item.get("suggestion"), item.get("advice"), item.get("fix"), item.get("solution"), item.get("建议"), item.get("修改建议")]
            ),
        }
    text = _first_non_empty([item]) or default_title
    return {"title": text, "level": "", "problem": text, "suggestion": ""}


def _build_report_payload(job: ContractJob) -> dict:
    result_json = job.result_json if isinstance(job.result_json, dict) else {}
    raw_markdown = (job.result_markdown or "").strip()

    if not result_json:
        return {
            "contract_type": "未识别",
            "confidence_text": "-",
            "type_source": "markdown",
            "stamp_text": "未提及",
            "stamp_color": "#6b7280",
            "key_facts": {
                "合同名称": "未提及",
                "甲方": "未提及",
                "乙方": "未提及",
                "金额": "未提及",
                "期限": "未提及",
            },
            "overview": raw_markdown or "暂无概述",
            "risks": [],
            "improvements": [],
        }

    contract_type = _first_non_empty([result_json.get("合同类型"), result_json.get("contract_type"), result_json.get("type"), result_json.get("type_l2")]) or "未识别"
    type_detail = result_json.get("合同类型明细")
    if not isinstance(type_detail, dict):
        type_detail = result_json.get("contract_type_detail")
    if not isinstance(type_detail, dict):
        type_detail = result_json.get("type_detail")
    if not isinstance(type_detail, dict):
        type_detail = {}

    conf_text = "-"
    conf = type_detail.get("confidence")
    if isinstance(conf, (int, float)):
        conf_text = f"{round(float(conf) * 100)}%"
    elif conf is not None:
        conf_text = str(conf)

    stamp_val = result_json.get("是否盖章")
    if not stamp_val:
        stamp_status = result_json.get("stamp_status")
        if stamp_status == "YES":
            stamp_val = "是"
        elif stamp_status == "NO":
            stamp_val = "否"
        elif stamp_status == "UNCERTAIN":
            stamp_val = "不确定"
    stamp_text = _first_non_empty([stamp_val, "未提及"])
    if stamp_text in ("是", "YES", "True", "true"):
        stamp_color = "#0c7b48"
    elif stamp_text in ("否", "NO", "False", "false"):
        stamp_color = "#b42318"
    else:
        stamp_color = "#6b7280"

    key_facts_raw = result_json.get("key_facts")
    if not isinstance(key_facts_raw, dict):
        key_facts_raw = {}
    key_facts = {
        "合同名称": _first_non_empty([key_facts_raw.get("合同名称"), key_facts_raw.get("协议名称"), key_facts_raw.get("contract_name"), key_facts_raw.get("name"), "未提及"]),
        "甲方": _first_non_empty([key_facts_raw.get("甲方"), key_facts_raw.get("甲方名称"), key_facts_raw.get("partyA"), key_facts_raw.get("party_a"), "未提及"]),
        "乙方": _first_non_empty([key_facts_raw.get("乙方"), key_facts_raw.get("乙方名称"), key_facts_raw.get("partyB"), key_facts_raw.get("party_b"), "未提及"]),
        "金额": _first_non_empty([key_facts_raw.get("金额"), key_facts_raw.get("合同金额"), key_facts_raw.get("总金额"), key_facts_raw.get("amount"), "未提及"]),
        "期限": _first_non_empty([key_facts_raw.get("期限"), key_facts_raw.get("合同期限"), key_facts_raw.get("有效期"), key_facts_raw.get("term"), "未提及"]),
    }

    overview = _first_non_empty(
        [
            result_json.get("审查概述"),
            result_json.get("overview"),
            result_json.get("summary"),
            (result_json.get("result") or {}).get("overview") if isinstance(result_json.get("result"), dict) else None,
            raw_markdown,
        ]
    ) or "暂无概述"

    risks = [_normalize_review_item(item, "风险点") for item in _extract_review_items(result_json, ["风险点", "risks", "risk_points", "风险点及建议"])]
    # 风险点区块只展示风险本身，不展示建议文本。
    for _item in risks:
        if isinstance(_item, dict):
            _item["suggestion"] = ""

    improvements = [
        _normalize_review_item(item, "改进建议")
        for item in _extract_review_items(
            result_json,
            ["改进建议", "改进措施", "improvements", "improvement", "improvement_suggestions", "suggestions", "recommendations", "优化建议", "完善建议", "修改建议", "审查建议"],
        )
    ]

    return {
        "contract_type": contract_type,
        "confidence_text": conf_text,
        "type_source": _first_non_empty([type_detail.get("source"), "result_json"]),
        "stamp_text": stamp_text,
        "stamp_color": stamp_color,
        "key_facts": key_facts,
        "overview": overview,
        "risks": risks,
        "improvements": improvements,
    }


def _render_items_md(title: str, items: list[dict]) -> str:
    lines = [f"## {title}"]
    if not items:
        lines.append("- 未识别到相关内容")
        return "\n".join(lines)
    for idx, item in enumerate(items, 1):
        head = _first_non_empty([item.get("title"), title])
        level = _first_non_empty([item.get("level")])
        problem = _first_non_empty([item.get("problem")])
        suggestion = _first_non_empty([item.get("suggestion")])
        title_line = f"{idx}. {head}"
        if level:
            title_line += f"（{level}）"
        lines.append(title_line)
        if problem:
            lines.append(f"   - 问题：{problem}")
        if suggestion:
            lines.append(f"   - {suggestion}")
    return "\n".join(lines)


def _render_items_html(title: str, items: list[dict]) -> str:
    if not items:
        return f"<h3>{escape(title)}</h3><div class='empty'>未识别到相关内容</div>"

    lines = [f"<h3>{escape(title)}</h3><ol>"]
    for item in items:
        head = escape(_first_non_empty([item.get("title"), title]))
        level = _first_non_empty([item.get("level")])
        problem = _first_non_empty([item.get("problem")])
        suggestion = _first_non_empty([item.get("suggestion")])
        if level:
            head += f" <span class='muted'>({escape(level)})</span>"
        lines.append("<li>")
        lines.append(f"<div><b>{head}</b></div>")
        if problem:
            lines.append(f"<div><b>问题：</b>{escape(problem)}</div>")
        if suggestion:
            lines.append(f"<div>{escape(suggestion)}</div>")
        lines.append("</li>")
    lines.append("</ol>")
    return "".join(lines)


def _build_review_markdown(job: ContractJob, payload: Optional[dict] = None) -> str:
    report = payload or _build_report_payload(job)
    lines = [
        "# 合同审查报告",
        "",
        f"- 合同类型：{report.get('contract_type', '未识别')}",
        f"- 盖章：{report.get('stamp_text', '未提及')}",
        f"- 类型置信度：{report.get('confidence_text', '-')}",
        f"- 类型来源：{report.get('type_source', 'result_json')}",
        "",
        "## 合同关键要素",
    ]
    for k, v in (report.get("key_facts") or {}).items():
        lines.append(f"- {k}：{v}")
    lines.extend(
        [
            "",
            "## 审查概述",
            str(report.get("overview", "暂无概述")),
            "",
            _render_items_md("风险点", report.get("risks") or []),
            "",
            _render_items_md("改进建议", report.get("improvements") or []),
            "",
        ]
    )
    return "\n".join(lines).strip()


def _build_review_html(job: ContractJob, payload: Optional[dict] = None) -> str:
    report = payload or _build_report_payload(job)
    facts_rows = "".join(f"<tr><td class='k'>{escape(str(k))}</td><td>{escape(str(v))}</td></tr>" for k, v in (report.get("key_facts") or {}).items())
    risks_html = _render_items_html("风险点", report.get("risks") or [])
    improve_html = _render_items_html("改进建议", report.get("improvements") or [])
    stamp_color = report.get("stamp_color", "#6b7280")

    return f"""
<style>
body {{ font-family: 'Microsoft YaHei','PingFang SC',sans-serif; color:#0f172a; }}
.title {{ font-size:24px; font-weight:800; margin:0 0 10px; color:#4b6fae; }}
.meta {{ color:#64748b; margin:2px 0 10px; font-size:13px; }}
.panel {{
  border:1px solid #d6e3e0; border-radius:12px; background:#ffffff;
  padding:12px 14px; margin:0 0 12px;
}}
.panel h3 {{ margin:0 0 8px; color:#2f4b6e; }}
.stamp {{ font-weight:800; color:{stamp_color}; }}
table {{ border-collapse:collapse; width:100%; }}
td {{ padding:7px 6px; border-bottom:1px solid #edf2f7; vertical-align:top; }}
td.k {{ width:120px; color:#475569; font-weight:700; }}
ol {{ margin:8px 0 0; padding-left:20px; }}
li {{ margin:8px 0; line-height:1.6; }}
.muted {{ color:#64748b; font-weight:400; }}
.empty {{ color:#64748b; font-size:13px; }}
</style>
<div class='title'>合同审查报告</div>
<div class='meta'><b>合同类型：</b>{escape(str(report.get('contract_type', '未识别')))} | <b>盖章：</b><span class='stamp'>{escape(str(report.get('stamp_text', '未提及')))}</span></div>
<div class='meta'><b>类型置信度：</b>{escape(str(report.get('confidence_text', '-')))} | <b>类型来源：</b>{escape(str(report.get('type_source', 'result_json')))}</div>
<div class='panel'><h3>合同关键要素</h3><table>{facts_rows}</table></div>
<div class='panel'><h3>审查概述</h3><div>{escape(str(report.get('overview', '暂无概述')))}</div></div>
<div class='panel'>{risks_html}</div>
<div class='panel'>{improve_html}</div>
"""


@require_http_methods(["GET"])
def export_pdf(request, job_id: int):
    try:
        job = ContractJob.objects.get(id=job_id)
    except ContractJob.DoesNotExist:
        return JsonResponse({"ok": False, "error": "job not found"}, status=404)

    report_payload = _build_report_payload(job)
    report_markdown = _build_review_markdown(job, payload=report_payload)
    if not report_markdown:
        return JsonResponse({"ok": False, "error": "job has no report content yet"}, status=400)

    html = PDF_HTML_TEMPLATE.render(body=_build_review_html(job, payload=report_payload))

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
