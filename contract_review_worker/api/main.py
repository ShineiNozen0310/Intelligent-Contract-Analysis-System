# ✅ 保留 MinerU accurate（未来 GPU 更快更准）
# ✅ 预埋：GPU env 透传（MINERU_DEVICE / CUDA_VISIBLE_DEVICES / HF_HOME 等）
# ✅ 防“device 污染”：把任何可能的 'ocr'/'OCR' 当作非法 device，强制回退 cpu
# ✅ MinerU：流式输出 + 超时
# ✅ auto：fast OCR（PaddleOCR）不足则 fallback 到 MinerU
#
# 未来上 GPU 时，仅需在 .env 加：
#   CUDA_VISIBLE_DEVICES=0
#   MINERU_DEVICE=cuda
# 并确保 torch.cuda.is_available() 为 True（安装支持你显卡的 torch+cuda 版本）
#
# 建议 .env（关键项）：
#   REVIEW_MODE=auto
#   DJANGO_CALLBACK_URL=http://127.0.0.1:8000/contract/api/job/update/
#   PDFTOPPM_CMD=...
#   OCR_DPI=350
#   OCR_LANG=ch
#   PADDLE_OCR_USE_GPU=1
#   AUTO_FALLBACK_MIN_CHARS=200
#   MINERU_TIMEOUT=900
#   QWEN_TIMEOUT=180

from __future__ import annotations

import concurrent.futures
from collections import deque
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from contract_review_worker.app_config import bootstrap
from contract_review_worker.celery_app import app as celery_app
from packages.core_engine.result_contract import build_error_result, merge_stamp_result
from .llm_provider import review_contract, fix_ocr_text

BASE_DIR = Path(__file__).resolve().parents[2]
bootstrap(BASE_DIR)

app = FastAPI()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("contract_review_worker")
_CALLBACK_SESSION = requests.Session()
# Keep handles alive for os.add_dll_directory on Windows.
_DLL_DIR_HANDLES: List[Any] = []

_OCR_QUALITY_KEYWORDS = (
    "合同",
    "协议",
    "甲方",
    "乙方",
    "金额",
    "违约",
    "争议",
    "管辖",
    "验收",
    "发票",
    "支付",
    "期限",
    "保密",
    "知识产权",
)

_OCR_POST_REPLACEMENTS = (
    ("跑口记者", "驻点记者"),
    ("微文题月", "微文标题"),
    ("谷口微信", "各口微信"),
    ("勾服", "克服"),
    ("微文", "微信文章"),
    ("密传", "宣传"),
    ("字传", "宣传"),
)

_OCR_POST_REGEX_REPLACEMENTS = (
    (r"违约[参爹伞令]", "违约金"),
    (r"¥\s*([0-9]{1,3}(?:[,，][0-9]{3})*(?:\.[0-9]{1,2})?)", r"¥\1"),
)

_OCR_HARD_BLOCK_TERMS = tuple(bad for bad, _ in _OCR_POST_REPLACEMENTS)


class AnalyzeReq(BaseModel):
    job_id: int
    pdf_path: str
    out_root: str = ""


@app.get("/healthz")
def healthz():
    return {"ok": True}


# =========================
# mode
# =========================
def _review_mode() -> str:
    return (os.environ.get("REVIEW_MODE") or "auto").strip().lower()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _clip_markdown_for_callback(text: str, is_error: bool = False) -> str:
    if not text:
        return ""
    default_limit = 20000 if is_error else 120000
    max_chars = _env_int("WORKER_RESULT_MARKDOWN_MAX_CHARS", default_limit)
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


# =========================
# callback
# =========================
def _callback_url() -> str:
    url = os.environ.get("DJANGO_CALLBACK_URL") or "http://127.0.0.1:8000/contract/api/job/update/"
    return url.strip().rstrip("/") + "/"


def notify_django(payload: dict) -> None:
    url = _callback_url()
    token = (os.environ.get("WORKER_TOKEN") or "").strip()
    headers = {"X-Worker-Token": token} if token else {}
    attempts = _env_int("WORKER_CALLBACK_RETRY", 3)
    backoff = float(os.environ.get("WORKER_CALLBACK_BACKOFF", "1") or "1")
    timeout_s = _env_int("WORKER_CALLBACK_TIMEOUT", 20)

    last_err: Exception | None = None
    for i in range(max(attempts, 1)):
        try:
            r = _CALLBACK_SESSION.post(url, json=payload, timeout=timeout_s, headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"non-200: {r.status_code} {r.text[:400]}")
            logger.info(
                "[callback] ok: job=%s status=%s progress=%s stage=%s",
                payload.get("job_id"),
                payload.get("status"),
                payload.get("progress"),
                payload.get("stage"),
            )
            return
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(backoff * (2**i))
                continue
            logger.error("[callback] failed after retries: %s", e)


# =========================
# subprocess runners
# =========================
def _run_capture(cmd: List[str], cwd: Optional[str] = None, name: str = "", env: Optional[dict] = None) -> None:
    proc_encoding = (os.environ.get("WORKER_SUBPROCESS_ENCODING") or "utf-8").strip() or "utf-8"
    verbose = _env_flag("WORKER_VERBOSE_SUBPROCESS", False)
    if verbose:
        print(f"=== {name or 'cmd'} ===", " ".join(cmd), flush=True)
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding=proc_encoding,
            errors="replace",
            cwd=cwd,
            env=env,
        )
        if p.stdout:
            print(f"=== {name or 'stdout'} ===\n{p.stdout}", flush=True)
        if p.stderr:
            print(f"=== {name or 'stderr'} ===\n{p.stderr}", flush=True)
        if p.returncode != 0:
            raise RuntimeError(f"{name or 'command'} failed: code={p.returncode}")
        return

    p = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding=proc_encoding,
        errors="replace",
        cwd=cwd,
        env=env,
    )
    if p.returncode != 0:
        err_text = (p.stderr or "").strip()
        if len(err_text) > 800:
            err_text = err_text[-800:]
        raise RuntimeError(f"{name or 'command'} failed: code={p.returncode} {err_text}")


def _run_stream(
    cmd: List[str],
    cwd: Optional[str] = None,
    name: str = "",
    timeout: Optional[int] = None,
    env: Optional[dict] = None,
    tail_lines: int = 80,
) -> None:
    proc_encoding = (os.environ.get("WORKER_SUBPROCESS_ENCODING") or "utf-8").strip() or "utf-8"
    stream_log = _env_flag("WORKER_VERBOSE_STREAM", True)
    effective_tail = _env_int("WORKER_STREAM_TAIL_LINES", tail_lines)
    if effective_tail < 0:
        effective_tail = 0

    print(f"=== {name or 'cmd'} ===", " ".join(cmd), flush=True)

    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=proc_encoding,
        errors="replace",
        bufsize=1,
        universal_newlines=True,
        env=env,
    )

    tail: deque[str] = deque(maxlen=effective_tail if effective_tail > 0 else None)
    try:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip("\n")
            if stream_log:
                print(line, flush=True)
            if effective_tail > 0:
                tail.append(line)

        rc = p.wait(timeout=timeout)
        if rc != 0:
            tail_text = "\n".join(tail)
            raise RuntimeError(f"{name or 'command'} failed: code={rc}\n{tail_text}")

    except subprocess.TimeoutExpired:
        p.kill()
        raise RuntimeError(f"{name or 'command'} timeout after {timeout}s")


def _is_cuda_failure(err: Exception) -> bool:
    msg = str(err).lower()
    needles = [
        "cuda",
        "cublas",
        "cudnn",
        "cufft",
        "cusolver",
        "out of memory",
        "oom",
        "illegal memory access",
        "device-side assert",
        "no kernel image",
        "unspecified launch failure",
        "access violation",
        "0xc0000005",
        "0xc0000409",
        "code=3221225477",
        "code=3221226505",
    ]
    return any(k in msg for k in needles)


# =========================
# GPU env pass-through + device sanitize
# =========================
_VALID_TORCH_DEVICES = {
    "cpu",
    "cuda",
    "cuda:0",
    "cuda:1",
    "cuda:2",
    "cuda:3",
    "mps",
}


def _sanitize_mineru_device(raw: str) -> str:
    """
    防止历史 bug：device 被污染成 'ocr' 等非法值，导致 transformers torch.device 报错。
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""

    # 明确拒绝常见污染值
    if s in {"ocr", "paddleocr", "pdftoppm", "poppler"}:
        return "cpu"

    # 允许 cpu/cuda/cuda:0/mps
    if s in _VALID_TORCH_DEVICES:
        return s

    # 允许形如 cuda:0
    if re.fullmatch(r"cuda:\d+", s):
        return s

    # 其他未知 -> cpu
    return "cpu"


def _append_pythonpath(env: dict, path: Path) -> None:
    if not path.exists():
        return
    cur = env.get("PYTHONPATH", "")
    parts = [p for p in cur.split(os.pathsep) if p] if cur else []
    pstr = str(path)
    if pstr not in parts:
        parts.insert(0, pstr)
        env["PYTHONPATH"] = os.pathsep.join(parts)


def _prepend_path(env: dict, path: Path) -> None:
    if not path.exists():
        return
    cur = env.get("PATH", "")
    parts = [p for p in cur.split(os.pathsep) if p] if cur else []
    pstr = str(path)
    if pstr not in parts:
        env["PATH"] = pstr + (os.pathsep + cur if cur else "")


def _mineru_env(force_device: Optional[str] = None) -> dict:
    """
    透传 GPU/缓存相关 env 给 mineru 进程，并做 device 防污染。
    你未来只用改 .env：
      CUDA_VISIBLE_DEVICES=0
      MINERU_DEVICE=cuda
      HF_HOME=...
    """
    env = os.environ.copy()

    # 统一 device：用 MINERU_DEVICE，不要复用 OCR 的任何字段
    raw_device = force_device if force_device is not None else env.get("MINERU_DEVICE", "")
    device = _sanitize_mineru_device(raw_device)
    if device:
        env["MINERU_DEVICE"] = device
        if device == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""

    # 防止旧 env 里有人写了 DEVICE=ocr 之类污染：
    # 如果你工程里有 DEVICE 这个变量，且值明显不合法，则删掉它，避免被第三方读到。
    maybe_device = (env.get("DEVICE") or "").strip().lower()
    if maybe_device and maybe_device not in _VALID_TORCH_DEVICES and not re.fullmatch(r"cuda:\d+", maybe_device):
        # 直接移除，避免被某些库误读
        env.pop("DEVICE", None)

    # Optional: allow local mineru package resolution when running via python -m
    _append_pythonpath(env, BASE_DIR)
    _append_pythonpath(env, BASE_DIR / "parsers" / "mineru")

    # Optional: ensure torch DLLs are discoverable in child process (Windows)
    try:
        import torch  # type: ignore

        torch_lib = Path(torch.__file__).parent / "lib"
        _prepend_path(env, torch_lib)
    except Exception:
        pass

    # 可选：如果你未来想把 transformers 缓存放到项目目录
    # env.setdefault("HF_HOME", str(BASE_DIR / ".hf_cache"))

    return env


# =========================
# MinerU (accurate)
# =========================
def _resolve_mineru_cmd() -> List[str]:
    use_python = (os.environ.get("MINERU_USE_PYTHON") or "1").strip().lower() in {"1", "true", "yes", "y", "on"}
    if use_python:
        try:
            import importlib.util

            if importlib.util.find_spec("mineru.cli.client") is not None:
                return [sys.executable, "-m", "mineru.cli.client"]
        except Exception:
            pass

    p = shutil.which("mineru")
    if p:
        return [p]
    scripts_dir = Path(sys.executable).parent
    exe = scripts_dir / ("mineru.exe" if os.name == "nt" else "mineru")
    if exe.exists():
        return [str(exe)]
    raise FileNotFoundError("mineru executable not found (PATH and venv Scripts both missing)")


def run_mineru_cli(pdf_path: str, out_dir: str, force_device: Optional[str] = None) -> Path:
    mineru_cmd = _resolve_mineru_cmd()
    out_dir_path = Path(out_dir).resolve()
    out_dir_path.mkdir(parents=True, exist_ok=True)

    pdf_abs = str(Path(pdf_path).resolve())
    cmd = mineru_cmd + ["-p", pdf_abs, "-o", str(out_dir_path)]

    timeout = int(os.environ.get("MINERU_TIMEOUT") or "900")
    env = _mineru_env(force_device=force_device)
    _run_stream(cmd, cwd=str(out_dir_path), name="mineru", timeout=timeout, env=env)
    return out_dir_path


def _find_largest_md(out_dir: Path) -> Optional[Path]:
    mds = list(out_dir.rglob("*.md"))
    if not mds:
        return None
    mds.sort(key=lambda p: p.stat().st_size, reverse=True)
    return mds[0]


def _strip_md_images(md: str) -> str:
    md = re.sub(r"!\[[^\]]*]\([^)]+\)", "", md)
    md = re.sub(r"!\[[^\]]*]\[[^\]]+\]", "", md)
    return md


# =========================
# FAST OCR
# =========================
def _resolve_poppler_pdftoppm() -> Optional[str]:
    cmd = (os.environ.get("PDFTOPPM_CMD") or "").strip().strip('"').strip("'")
    if cmd and Path(cmd).exists():
        return cmd
    return shutil.which("pdftoppm")


def _normalize_paddle_lang(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "ch"

    if s in {"chi_sim+eng", "chi_sim", "chi_tra", "zh", "zh-cn", "zh_hans", "chinese", "ch"}:
        return "ch"
    if s in {"eng", "en"}:
        return "en"

    if "+" in s:
        parts = [p.strip() for p in s.split("+") if p.strip()]
        if any(p in {"chi_sim", "chi_tra", "ch", "zh", "zh-cn"} for p in parts):
            return "ch"
        if any(p in {"eng", "en"} for p in parts):
            return "en"

    return s


def _resolve_paddleocr_home() -> Optional[Path]:
    raw = (os.environ.get("PADDLEOCR_HOME") or "").strip().strip('"').strip("'")
    if not raw:
        return None
    p = Path(raw).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _prepend_os_path(path: Path) -> None:
    if not path.exists():
        return
    pstr = str(path)
    cur = os.environ.get("PATH", "")
    parts = [p for p in cur.split(os.pathsep) if p] if cur else []
    if pstr in parts:
        return
    os.environ["PATH"] = pstr + (os.pathsep + cur if cur else "")


def _register_dll_dir(path: Path) -> None:
    if os.name != "nt":
        return
    if not path.exists():
        return
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    try:
        handle = add_dll_directory(str(path))
        _DLL_DIR_HANDLES.append(handle)
    except Exception:
        pass


def _prepare_paddle_gpu_runtime_env() -> None:
    if not _env_flag("PADDLE_GPU_AUTO_PATH", True):
        return

    roots: List[Path] = []
    try:
        roots.append(Path(sys.executable).resolve().parent.parent)
    except Exception:
        pass
    roots.append(BASE_DIR / ".venv")

    rel_dirs = (
        # Paddle cu13 aggregate runtime layout
        Path("Lib") / "site-packages" / "nvidia" / "cu13" / "bin" / "x86_64",
        Path("Lib") / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path("Lib") / "site-packages" / "nvidia" / "cublas" / "bin",
        Path("Lib") / "site-packages" / "nvidia" / "cuda_runtime" / "bin",
        Path("Lib") / "site-packages" / "nvidia" / "cuda_nvrtc" / "bin",
        # Paddle cuDNN 9 may need zlibwapi.dll from torch/lib on Windows.
        Path("Lib") / "site-packages" / "torch" / "lib",
    )

    seen = set()
    for root in roots:
        for rel in rel_dirs:
            d = (root / rel).resolve()
            key = str(d).lower()
            if key in seen:
                continue
            if d.exists():
                _prepend_os_path(d)
                _register_dll_dir(d)
                seen.add(key)


@lru_cache(maxsize=8)
def _load_paddle_ocr(lang: str, use_gpu: bool, use_angle_cls: bool):
    _resolve_paddleocr_home()
    if use_gpu:
        _prepare_paddle_gpu_runtime_env()

    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "PaddleOCR not installed. Please install dependencies: paddleocr and paddlepaddle."
        ) from e

    show_log = _env_flag("PADDLE_OCR_SHOW_LOG", False)
    device = "gpu" if use_gpu else "cpu"
    init_kwargs_candidates: List[Dict[str, Any]] = [
        {"lang": lang, "use_angle_cls": use_angle_cls, "use_gpu": use_gpu, "show_log": show_log},
        {"lang": lang, "use_angle_cls": use_angle_cls, "use_gpu": use_gpu},
        {"lang": lang, "use_angle_cls": use_angle_cls, "device": device},
        {"lang": lang, "use_textline_orientation": use_angle_cls, "device": device},
        {"lang": lang, "device": device},
        {"lang": lang, "use_angle_cls": use_angle_cls},
        {"lang": lang},
    ]
    last_err: Exception | None = None
    for kwargs in init_kwargs_candidates:
        try:
            return PaddleOCR(**kwargs)
        except Exception as e:
            last_err = e
            continue

    if last_err is not None:
        raise RuntimeError(f"PaddleOCR init failed: {last_err}") from last_err
    raise RuntimeError("PaddleOCR init failed: unknown error")


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _extract_paddle_ocr_text(result: Any, min_score: float = 0.0) -> str:
    lines: List[tuple[float, float, str]] = []

    def _looks_like_box(box: Any) -> bool:
        if not isinstance(box, (list, tuple)) or len(box) < 2:
            return False
        pt_like = 0
        for pt in box:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                x = _to_float(pt[0])
                y = _to_float(pt[1])
                if x is not None and y is not None:
                    pt_like += 1
        return pt_like >= 2

    def _append_line(box: Any, text: str, score: Optional[float]) -> None:
        t = (text or "").strip()
        if not t:
            return
        if min_score > 0 and score is not None and score < min_score:
            return

        x = 0.0
        y = 0.0
        if isinstance(box, (list, tuple)):
            xs: List[float] = []
            ys: List[float] = []
            for pt in box:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    px = _to_float(pt[0])
                    py = _to_float(pt[1])
                    if px is not None and py is not None:
                        xs.append(px)
                        ys.append(py)
            if xs and ys:
                x = min(xs)
                y = min(ys)
        lines.append((y, x, t))

    def _walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, dict):
            text = node.get("text") or node.get("rec_text") or node.get("transcription")
            if isinstance(text, str):
                _append_line(node.get("box") or node.get("points"), text, _to_float(node.get("score")))
                return
            for val in node.values():
                if isinstance(val, (list, tuple, dict)):
                    _walk(val)
            return
        if isinstance(node, (list, tuple)):
            if len(node) == 2 and _looks_like_box(node[0]):
                rec = node[1]
                if isinstance(rec, (list, tuple)) and rec:
                    if not isinstance(rec[0], str):
                        return
                    txt = rec[0]
                    score = _to_float(rec[1]) if len(rec) > 1 else None
                    _append_line(node[0], txt, score)
                    return
                if isinstance(rec, str):
                    _append_line(node[0], rec, None)
                    return
            if len(node) == 2 and isinstance(node[0], str):
                _append_line(None, node[0], _to_float(node[1]))
                return
            for child in node:
                if isinstance(child, (list, tuple, dict)):
                    _walk(child)

    _walk(result)
    if not lines:
        return ""

    lines.sort(key=lambda it: (it[0], it[1]))
    merge_tol = _env_float("PADDLE_OCR_LINE_MERGE_TOL", 12.0)
    merged: List[List[Any]] = []
    for y, _x, text in lines:
        if not merged:
            merged.append([y, [text]])
            continue
        if abs(y - merged[-1][0]) <= merge_tol:
            merged[-1][1].append(text)
        else:
            merged.append([y, [text]])

    return "\n".join(" ".join(parts).strip() for _y, parts in merged if parts).strip()


def _paddle_ocr_image_text(ocr_engine: Any, img_path: Path, use_angle_cls: bool, min_score: float) -> str:
    try:
        result = ocr_engine.ocr(str(img_path), cls=use_angle_cls)
    except Exception as e:
        raise RuntimeError(f"paddleocr failed on {img_path.name}: {e}") from e
    return _extract_paddle_ocr_text(result, min_score=min_score)


def _ocr_quality_metrics(text: str) -> Dict[str, Any]:
    src = (text or "").strip()
    if not src:
        return {
            "score": 0.0,
            "char_count": 0,
            "line_count": 0,
            "han_ratio": 0.0,
            "alpha_ratio": 0.0,
            "garbage_ratio": 1.0,
            "keyword_hits": 0,
            "repeat_chunks": 0,
        }

    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    char_count = len(src)
    line_count = len(lines)

    han_count = 0
    alpha_count = 0
    garbage_count = 0
    allowed_punct = set("，。、《》：；、（）()【】“”‘’—…·,.!?;:%￥¥+-_/\\|@#*&=~'\"`")
    for ch in src:
        if "\u4e00" <= ch <= "\u9fff":
            han_count += 1
            continue
        if ch.isalpha():
            alpha_count += 1
            continue
        if ch.isdigit() or ch.isspace() or ch in allowed_punct:
            continue
        garbage_count += 1

    han_ratio = han_count / max(1, char_count)
    alpha_ratio = alpha_count / max(1, char_count)
    garbage_ratio = garbage_count / max(1, char_count)
    keyword_hits = sum(1 for kw in _OCR_QUALITY_KEYWORDS if kw in src)
    repeat_chunks = len(re.findall(r"(.)\1{5,}", src))

    length_score = min(char_count / 320.0, 1.0)
    line_score = min(line_count / 14.0, 1.0)
    lang_ratio = max(han_ratio, alpha_ratio)
    lang_score = min(lang_ratio / 0.32, 1.0)
    keyword_score = min(keyword_hits / 4.0, 1.0)
    garbage_penalty = min(garbage_ratio * 2.2, 0.55)
    repeat_penalty = min(repeat_chunks * 0.05, 0.2)

    score = (0.24 * length_score) + (0.14 * line_score) + (0.22 * lang_score) + (0.40 * keyword_score)
    score -= (garbage_penalty + repeat_penalty)
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 4),
        "char_count": char_count,
        "line_count": line_count,
        "han_ratio": round(han_ratio, 4),
        "alpha_ratio": round(alpha_ratio, 4),
        "garbage_ratio": round(garbage_ratio, 4),
        "keyword_hits": keyword_hits,
        "repeat_chunks": repeat_chunks,
    }


def _pick_best_ocr_candidate(candidates: List[tuple[str, str]]) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    best_text = ""
    best_metric = _ocr_quality_metrics("")
    all_metrics: List[Dict[str, Any]] = []

    for label, text in candidates:
        metric = _ocr_quality_metrics(text)
        metric["candidate"] = label
        all_metrics.append(metric)

        better_score = metric["score"] > best_metric["score"]
        tie_score = metric["score"] == best_metric["score"] and metric["char_count"] > best_metric["char_count"]
        if better_score or tie_score:
            best_text = text
            best_metric = metric

    return best_text, best_metric, all_metrics


def _normalize_ocr_text(text: str) -> str:
    src = (text or "").strip()
    if not src:
        return ""

    out = unicodedata.normalize("NFKC", src)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)

    if _env_flag("OCR_POST_CORRECT", True):
        for bad, good in _OCR_POST_REPLACEMENTS:
            out = out.replace(bad, good)
        for pat, repl in _OCR_POST_REGEX_REPLACEMENTS:
            out = re.sub(pat, repl, out)

    return out.strip()


def _ocr_zero_tolerance_guard(text: str) -> Dict[str, Any]:
    src = (text or "").strip()
    metrics = _ocr_quality_metrics(src)
    min_score = _env_float("OCR_ZERO_MIN_SCORE", 0.78)
    max_unknown_chars = _env_int("OCR_ZERO_MAX_UNKNOWN_CHARS", 2)

    bad_term_hits = [term for term in _OCR_HARD_BLOCK_TERMS if term and term in src]
    unknown_chars = []
    for ch in src:
        if ch in {"\ufffd", "�"}:
            unknown_chars.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in {"\n", "\t", "\r"}:
            unknown_chars.append(ch)

    ok = True
    reasons: List[str] = []
    if metrics["score"] < min_score:
        ok = False
        reasons.append("low_ocr_score")
    if bad_term_hits:
        ok = False
        reasons.append("hard_block_terms")
    if len(unknown_chars) > max_unknown_chars:
        ok = False
        reasons.append("unknown_chars")

    return {
        "ok": ok,
        "score": metrics["score"],
        "min_score": min_score,
        "bad_terms": bad_term_hits,
        "unknown_char_count": len(unknown_chars),
        "max_unknown_chars": max_unknown_chars,
        "reasons": reasons,
    }


def _should_run_llm_ocr_fix(text: str) -> bool:
    if not _env_flag("OCR_LLM_FIX_ENABLED", True):
        return False
    src = (text or "").strip()
    if not src:
        return False
    metrics = _ocr_quality_metrics(src)
    min_score = _env_float("OCR_LLM_FIX_MIN_SCORE", 0.55)
    if metrics["score"] < min_score:
        return True
    for bad in _OCR_HARD_BLOCK_TERMS:
        if bad and bad in src:
            return True
    suspicious_pat = re.compile(r"(违约[参爹伞令]|[�\ufffd])")
    if suspicious_pat.search(src):
        return True
    return False


def _select_page_indices(total: int, max_pages: int, head_pages: int, tail_pages: int) -> List[int]:
    if total <= 0:
        return []
    if max_pages <= 0 or total <= max_pages:
        return list(range(total))

    head = min(head_pages, total)
    tail = min(tail_pages, max(0, total - head))

    keep = list(range(0, head))
    if tail > 0:
        keep += list(range(total - tail, total))

    remaining = max_pages - len(keep)
    middle_start = head
    middle_end = total - tail
    if remaining > 0 and middle_end > middle_start:
        step = (middle_end - middle_start) / float(remaining + 1)
        for i in range(remaining):
            idx = int(middle_start + step * (i + 1))
            keep.append(idx)

    return sorted(set(keep))


def _render_pdf_pages(pdf_path: str, work_dir: Path, dpi: int, gray: bool = True) -> List[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    prefix = work_dir / "page"
    pdftoppm = _resolve_poppler_pdftoppm()
    if not pdftoppm:
        raise RuntimeError("pdftoppm not found. Set PDFTOPPM_CMD or add poppler bin to PATH.")
    cmd = [str(pdftoppm), "-png"]
    if gray:
        cmd.append("-gray")
    cmd += ["-r", str(dpi), str(pdf_path), str(prefix)]
    _run_capture(cmd, cwd=str(work_dir), name="pdftoppm")

    imgs = sorted(work_dir.glob("page-*.png"))
    if not imgs:
        imgs = sorted(work_dir.glob("page*.png"))
    return imgs


def _sample_pdf_for_render(
    pdf_path: str,
    out_pdf: Path,
    max_pages: int,
    head_pages: int,
    tail_pages: int,
) -> tuple[str, Optional[List[int]]]:
    if max_pages <= 0:
        return pdf_path, None
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore

        reader = PdfReader(pdf_path)
        total = len(reader.pages)
        keep = _select_page_indices(total, max_pages, head_pages, tail_pages)
        if total <= max_pages or len(keep) >= total:
            return pdf_path, keep

        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        for i in keep:
            if 0 <= i < total:
                writer.add_page(reader.pages[i])
        with open(out_pdf, "wb") as f:
            writer.write(f)
        print(f"[sample_pdf] sampled pages {len(keep)}/{total}", flush=True)
        return str(out_pdf), keep
    except Exception as e:
        print(f"[sample_pdf] sample failed, fallback full pdf: {e}", flush=True)
        return pdf_path, None


def _preprocess_ocr_image(img_path: Path, work_dir: Path) -> Path:
    if not _env_flag("OCR_PREPROCESS", False):
        return img_path
    try:
        from PIL import Image, ImageFilter, ImageOps  # type: ignore

        threshold = _env_int("OCR_BINARIZE_THRESHOLD", 168)
        median_size = _env_int("OCR_MEDIAN_FILTER_SIZE", 3)
        sharpen = _env_flag("OCR_PREPROCESS_SHARPEN", True)

        if median_size < 3:
            median_size = 3
        if median_size % 2 == 0:
            median_size += 1
        median_size = min(median_size, 9)

        out_path = work_dir / f"{img_path.stem}_prep.png"
        with Image.open(img_path) as src:
            gray = src.convert("L")
            gray = ImageOps.autocontrast(gray, cutoff=1)
            gray = gray.filter(ImageFilter.MedianFilter(size=median_size))
            if sharpen:
                gray = gray.filter(ImageFilter.SHARPEN)
            bw = gray.point(lambda p: 255 if p >= threshold else 0, mode="1").convert("L")
            bw.save(out_path)
        return out_path
    except Exception as e:
        print(f"[ocr] preprocess failed for {img_path.name}: {e}", flush=True)
        return img_path


def _build_ocr_candidate_images(img_path: Path, work_dir: Path) -> List[Path]:
    if not _env_flag("OCR_PREPROCESS", False):
        return [img_path]

    try:
        from PIL import Image, ImageFilter, ImageOps  # type: ignore

        threshold = _env_int("OCR_BINARIZE_THRESHOLD", 166)
        median_size = _env_int("OCR_MEDIAN_FILTER_SIZE", 3)
        sharpen = _env_flag("OCR_PREPROCESS_SHARPEN", True)
        multi_threshold = _env_flag("OCR_MULTI_THRESHOLD", True)
        upscale_ratio = _env_float("OCR_UPSCALE_RATIO", 1.0)
        max_variants = _env_int("OCR_MAX_VARIANTS", 4)

        if median_size < 3:
            median_size = 3
        if median_size % 2 == 0:
            median_size += 1
        median_size = min(median_size, 9)
        if max_variants < 1:
            max_variants = 1

        with Image.open(img_path) as src:
            base = src.convert("L")
            base = ImageOps.autocontrast(base, cutoff=1)
            base = base.filter(ImageFilter.MedianFilter(size=median_size))
            if sharpen:
                base = base.filter(ImageFilter.SHARPEN)

            if upscale_ratio > 1.01:
                try:
                    resample = Image.Resampling.BICUBIC  # type: ignore[attr-defined]
                except Exception:
                    resample = Image.BICUBIC  # type: ignore[attr-defined]
                new_w = max(1, int(base.width * upscale_ratio))
                new_h = max(1, int(base.height * upscale_ratio))
                base = base.resize((new_w, new_h), resample=resample)

            out_paths: List[Path] = [img_path]
            seen = set()

            gray_out = work_dir / f"{img_path.stem}_prep_gray.png"
            base.save(gray_out)
            out_paths.append(gray_out)
            seen.add(gray_out.name)

            thresholds = [threshold]
            if multi_threshold:
                thresholds.extend([max(110, threshold - 12), min(220, threshold + 12)])

            for t in thresholds:
                bw = base.point(lambda p: 255 if p >= t else 0, mode="1").convert("L")
                out = work_dir / f"{img_path.stem}_prep_bw_{t}.png"
                if out.name in seen:
                    continue
                bw.save(out)
                out_paths.append(out)
                seen.add(out.name)
                if len(out_paths) >= max_variants:
                    break

            return out_paths[:max_variants]
    except Exception as e:
        print(f"[ocr] build variants failed for {img_path.name}: {e}", flush=True)
        return [img_path]


def _fast_ocr_pdf_to_text(
    pdf_path: str,
    work_dir: Path,
    page_images: Optional[List[Path]] = None,
    page_indices: Optional[List[int]] = None,
    force_use_gpu: Optional[bool] = None,
) -> str:
    pdftoppm = _resolve_poppler_pdftoppm()
    if not pdftoppm:
        raise RuntimeError("pdftoppm not found. Set PDFTOPPM_CMD or add poppler bin to PATH.")

    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_abs = str(Path(pdf_path).resolve())

    dpi = _env_int("OCR_DPI", 280)
    lang = _normalize_paddle_lang((os.environ.get("OCR_LANG") or os.environ.get("PADDLE_OCR_LANG") or "ch").strip())
    use_gpu = force_use_gpu if force_use_gpu is not None else _env_flag("PADDLE_OCR_USE_GPU", False)
    use_angle_cls = _env_flag("PADDLE_OCR_USE_ANGLE_CLS", True)
    min_score = _env_float("PADDLE_OCR_MIN_SCORE", 0.0)
    cleanup_rendered_images = _env_flag("OCR_CLEANUP_RENDERED_IMAGES", True)
    cleanup_shared_images = _env_flag("OCR_CLEANUP_SHARED_IMAGES", False)
    gpu_fallback_on_error = _env_flag("PADDLE_OCR_GPU_FALLBACK_CPU_ON_ERROR", True)
    gpu_fallback_on_quality = _env_flag("PADDLE_OCR_GPU_FALLBACK_CPU_ON_QUALITY", True)

    # 1) 选择页（可选：大文件抽样，提速）
    max_pages = _env_int("OCR_MAX_PAGES", 0)
    head_pages = _env_int("OCR_HEAD_PAGES", 6)
    tail_pages = _env_int("OCR_TAIL_PAGES", 2)

    if page_images is not None:
        if page_indices:
            imgs = [page_images[i] for i in page_indices if 0 <= i < len(page_images)]
        else:
            imgs = list(page_images)
        if not imgs:
            raise RuntimeError("no images provided for OCR")
        can_cleanup_images = cleanup_rendered_images and cleanup_shared_images
    else:
        pdf_for_ocr = pdf_abs
        if max_pages > 0:
            sampled_pdf = work_dir / "sampled.pdf"
            pdf_for_ocr, _ = _sample_pdf_for_render(pdf_abs, sampled_pdf, max_pages, head_pages, tail_pages)

        # 2) PDF -> PNG（灰度输出）
        imgs = _render_pdf_pages(pdf_for_ocr, work_dir, dpi, gray=True)
        if not imgs:
            raise RuntimeError("pdftoppm produced no images; cannot OCR.")
        can_cleanup_images = cleanup_rendered_images

    strict_mode = _env_flag("OCR_STRICT_MODE", True)
    early_accept_score = _env_float("OCR_VARIANT_EARLY_ACCEPT_SCORE", 0.90)

    # If GPU OCR quality check may trigger CPU retry, keep caller-provided images for retry.
    if use_gpu and page_images is not None and (gpu_fallback_on_error or gpu_fallback_on_quality):
        can_cleanup_images = False

    try:
        ocr_engine = _load_paddle_ocr(lang=lang, use_gpu=use_gpu, use_angle_cls=use_angle_cls)
        texts: List[str] = []

        for img in imgs:
            variant_imgs = _build_ocr_candidate_images(img, work_dir)
            generated_imgs = [p for p in variant_imgs if p != img]

            def _ocr_once(src_img: Path) -> str:
                return _paddle_ocr_image_text(
                    ocr_engine=ocr_engine,
                    img_path=src_img,
                    use_angle_cls=use_angle_cls,
                    min_score=min_score,
                )

            page_candidates: List[tuple[str, str]] = []
            try:
                for idx, variant_img in enumerate(variant_imgs):
                    cand_for_variant: List[tuple[str, str]] = []
                    txt_a = _ocr_once(variant_img)
                    cand_for_variant.append((f"v{idx}_paddle", txt_a))
                    best_variant_text, best_variant_metric, _ = _pick_best_ocr_candidate(cand_for_variant)

                    page_candidates.append((f"variant_{idx}", best_variant_text))
                    if (not strict_mode) and best_variant_metric["score"] >= early_accept_score:
                        break

                best_text, _, _ = _pick_best_ocr_candidate(page_candidates)
                best_text = _normalize_ocr_text(best_text)
                if best_text:
                    texts.append(best_text)
            finally:
                for prep_img in generated_imgs:
                    if prep_img.exists():
                        try:
                            prep_img.unlink()
                        except Exception:
                            pass
                if can_cleanup_images and img.exists():
                    try:
                        img.unlink()
                    except Exception:
                        pass

        final_text = _normalize_ocr_text("\n\n".join(texts))
    except Exception as e:
        if use_gpu and gpu_fallback_on_error:
            print(f"[ocr] gpu failed, fallback to cpu: {e}", flush=True)
            return _fast_ocr_pdf_to_text(
                pdf_path,
                work_dir,
                page_images=page_images,
                page_indices=page_indices,
                force_use_gpu=False,
            )
        raise

    if use_gpu:
        metrics = _ocr_quality_metrics(final_text)

        # Hard safety guard: if GPU OCR is obviously garbled, always fallback to CPU
        # to avoid catastrophic quality loss even when quality fallback is disabled.
        hard_min_score = _env_float("OCR_GPU_HARD_FALLBACK_SCORE", 0.35)
        hard_min_chars = _env_int("OCR_GPU_HARD_FALLBACK_MIN_CHARS", 600)
        hard_high_garbage = _env_float("OCR_GPU_HARD_FALLBACK_GARBAGE_RATIO", 0.08)
        hard_bad = (
            metrics["char_count"] >= hard_min_chars
            and (metrics["score"] < hard_min_score or metrics["garbage_ratio"] > hard_high_garbage)
        )
        if hard_bad:
            print(f"[ocr] gpu hard-fallback triggered {metrics}, retry cpu", flush=True)
            return _fast_ocr_pdf_to_text(
                pdf_path,
                work_dir,
                page_images=page_images,
                page_indices=page_indices,
                force_use_gpu=False,
            )

        if gpu_fallback_on_quality:
            min_gpu_score = _env_float("PADDLE_GPU_MIN_SCORE", 0.85)
            min_gpu_keywords = _env_int("PADDLE_GPU_MIN_KEYWORDS", 2)
            max_gpu_garbage = _env_float("PADDLE_GPU_MAX_GARBAGE_RATIO", 0.02)
            min_chars_for_quality = _env_int("PADDLE_GPU_QUALITY_MIN_CHARS", 1200)

            low_score = metrics["score"] < min_gpu_score
            low_keywords = metrics["char_count"] >= min_chars_for_quality and metrics["keyword_hits"] < min_gpu_keywords
            high_garbage = metrics["garbage_ratio"] > max_gpu_garbage

            if low_score or low_keywords or high_garbage:
                print(f"[ocr] gpu quality low {metrics}, fallback to cpu", flush=True)
                return _fast_ocr_pdf_to_text(
                    pdf_path,
                    work_dir,
                    page_images=page_images,
                    page_indices=page_indices,
                    force_use_gpu=False,
                )

    return final_text


def _pdf_page_count(pdf_path: str) -> int:
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(pdf_path).pages)
    except Exception:
        return -1


def _ensure_stamp2vec_in_path() -> None:
    stamp2vec_root = BASE_DIR / "parsers" / "stamp2vec"
    p = str(stamp2vec_root)
    if p not in sys.path:
        sys.path.insert(0, p)


def _normalize_device(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in {"cuda", "cuda:0", "0"}:
        return "cuda"
    if s in {"cpu", ""}:
        return "cpu"
    return s


@lru_cache(maxsize=2)
def _load_stamp2vec_pipeline(repo: str, filename: str, local_path: str, device: str):
    _ensure_stamp2vec_in_path()
    from pipelines.detection.yolo_stamp import YoloStampPipeline  # type: ignore

    if local_path:
        pipe = YoloStampPipeline.from_pretrained(local_model_path=local_path)
    else:
        pipe = YoloStampPipeline.from_pretrained(model_path_hf=repo, filename_hf=filename)

    if device:
        pipe.device = device
        try:
            pipe.model.to(device)
        except Exception:
            pass
    return pipe


@lru_cache(maxsize=2)
def _load_stamp_model(model_path: str):
    from ultralytics import YOLO  # type: ignore

    return YOLO(model_path)


def _detect_stamp_stamp2vec(page_images: List[Path]) -> Dict[str, Any]:
    enabled = (os.environ.get("STAMP_ENABLED") or "0").strip() in {"1", "true", "yes", "y", "on"}
    if not enabled:
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": "STAMP_ENABLED=0"}

    local_path = (os.environ.get("STAMP_YOLO_MODEL_PATH") or "").strip().strip('"').strip("'")
    repo = (os.environ.get("STAMP_HF_REPO") or "stamps-labs/yolo-stamp").strip()
    filename = (os.environ.get("STAMP_HF_FILENAME") or "weights.pt").strip()
    if local_path and not Path(local_path).exists():
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": f"model not found: {local_path}"}

    early_exit = (os.environ.get("STAMP_EARLY_EXIT") or "1").strip() in {"1", "true", "yes", "y", "on"}
    device = _normalize_device(os.environ.get("STAMP_DEVICE") or "")
    if not device:
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    try:
        # Tune stamp2vec thresholds via env (default lower than model constants for better recall)
        conf_env = os.environ.get("STAMP_S2V_THRESH") or os.environ.get("STAMP_YOLO_CONF") or "0.45"
        iou_env = os.environ.get("STAMP_S2V_IOU") or "0.3"
        try:
            conf_val = float(conf_env)
        except Exception:
            conf_val = 0.45
        try:
            iou_val = float(iou_env)
        except Exception:
            iou_val = 0.3
        try:
            from detection_models.yolo_stamp import constants as s2v_consts  # type: ignore
            from detection_models.yolo_stamp import utils as s2v_utils  # type: ignore

            if 0 < conf_val < 1:
                s2v_consts.OUTPUT_THRESH = conf_val
                s2v_utils.OUTPUT_THRESH = conf_val
            if 0 < iou_val < 1:
                s2v_consts.IOU_THRESH = iou_val
                s2v_utils.IOU_THRESH = iou_val
        except Exception:
            pass

        pipe = _load_stamp2vec_pipeline(repo, filename, local_path, device)
        evidence = []
        found = False
        from PIL import Image  # type: ignore

        for idx, img_path in enumerate(page_images):
            with Image.open(img_path) as src:
                img = src.convert("RGB")
            try:
                boxes = pipe(img)
            finally:
                try:
                    img.close()
                except Exception:
                    pass
            if boxes is not None and len(boxes) > 0:
                for b in boxes[:3]:
                    xyxy = b.tolist() if hasattr(b, "tolist") else list(b)
                    evidence.append({"page": idx + 1, "bbox": [int(x) for x in xyxy], "score": None})
                found = True
                if early_exit:
                    return {"stamp_status": "YES", "evidence": evidence}
        return {"stamp_status": "YES" if found else "NO", "evidence": evidence}
    except Exception as e:
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": str(e)}


def _detect_stamp_ultralytics(page_images: List[Path]) -> Dict[str, Any]:
    enabled = (os.environ.get("STAMP_ENABLED") or "0").strip() in {"1", "true", "yes", "y", "on"}
    if not enabled:
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": "STAMP_ENABLED=0"}

    model_path = (os.environ.get("STAMP_YOLO_MODEL_PATH") or "").strip().strip('"').strip("'")
    if not model_path:
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": "STAMP_YOLO_MODEL_PATH missing"}
    if not Path(model_path).exists():
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": f"model not found: {model_path}"}

    conf = float(os.environ.get("STAMP_YOLO_CONF") or "0.25")
    imgsz = int(os.environ.get("STAMP_IMG_SIZE") or "1024")
    early_exit = (os.environ.get("STAMP_EARLY_EXIT") or "1").strip() in {"1", "true", "yes", "y", "on"}

    device = (os.environ.get("STAMP_DEVICE") or "").strip()
    if not device:
        try:
            import torch  # type: ignore

            device = "0" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    try:
        model = _load_stamp_model(model_path)
        evidence = []
        found = False
        for idx, img in enumerate(page_images):
            results = model.predict(
                source=str(img),
                conf=conf,
                imgsz=imgsz,
                device=device,
                verbose=False,
            )
            if results and len(results) > 0 and getattr(results[0], "boxes", None) is not None:
                boxes = results[0].boxes
                if len(boxes) > 0:
                    for b in boxes[:3]:
                        xyxy = b.xyxy[0].tolist() if hasattr(b, "xyxy") else []
                        score = float(b.conf[0]) if hasattr(b, "conf") else None
                        evidence.append(
                            {"page": idx + 1, "bbox": [int(x) for x in xyxy] if xyxy else None, "score": score}
                        )
                    found = True
                    if early_exit:
                        return {"stamp_status": "YES", "evidence": evidence}
        return {"stamp_status": "YES" if found else "NO", "evidence": evidence}
    except Exception as e:
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": str(e)}


def _detect_stamp_yolo(page_images: List[Path]) -> Dict[str, Any]:
    backend = (os.environ.get("STAMP_BACKEND") or "stamp2vec").strip().lower()
    if backend in {"stamp2vec", "s2v"}:
        return _detect_stamp_stamp2vec(page_images)
    if backend in {"ultralytics", "yolov8"}:
        return _detect_stamp_ultralytics(page_images)
    return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": f"unknown backend: {backend}"}


def _detect_stamp_yolo_subprocess(page_images: List[Path], work_dir: Path) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    request_path = work_dir / "stamp_request.json"
    result_path = work_dir / "stamp_result.json"

    payload = {
        "images": [str(Path(p).resolve()) for p in page_images if Path(p).exists()],
    }
    request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "contract_review_worker.api.stamp_subprocess",
        "--request",
        str(request_path),
        "--output",
        str(result_path),
    ]

    timeout_s = _env_int("STAMP_SUBPROCESS_TIMEOUT", 240)
    proc_encoding = (os.environ.get("WORKER_SUBPROCESS_ENCODING") or "utf-8").strip() or "utf-8"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding=proc_encoding,
            errors="replace",
            timeout=timeout_s,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"stamp subprocess timeout after {timeout_s}s") from e

    if proc.returncode != 0:
        err_text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        if len(err_text) > 1200:
            err_text = err_text[-1200:]
        raise RuntimeError(f"stamp subprocess failed: code={proc.returncode} {err_text}")

    if not result_path.exists():
        raise RuntimeError("stamp subprocess finished without result file")

    raw = result_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        raise RuntimeError("stamp subprocess returned empty result")

    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"stamp subprocess returned invalid json: {raw[:300]}") from e

    if not isinstance(data, dict):
        raise RuntimeError("stamp subprocess result is not a json object")
    return data


def _fast_slice_text(text: str) -> Dict[str, Any]:
    max_chars = int(os.environ.get("FAST_MAX_CHARS") or "35000")
    max_lines = int(os.environ.get("FAST_MAX_LINES") or "1200")

    lines = text.splitlines()
    keep = [False] * len(lines)

    keywords = [
        "合同", "协议", "甲方", "乙方", "金额", "价款", "付款", "支付", "发票",
        "期限", "交付", "验收", "违约", "赔偿", "解除", "终止",
        "保密", "知识产权", "争议", "管辖", "仲裁", "法院",
    ]
    for i, line in enumerate(lines):
        if any(k in line for k in keywords):
            for j in range(max(0, i - 3), min(len(lines), i + 4)):
                keep[j] = True
    for i in range(min(80, len(lines))):
        keep[i] = True

    picked = [lines[i] for i in range(len(lines)) if keep[i]]
    if len(picked) < 40:
        picked = lines[:max_lines]
    if len(picked) > max_lines:
        picked = picked[:max_lines]

    sliced = "\n".join(picked)
    if len(sliced) > max_chars:
        sliced = sliced[:max_chars]

    return {
        "text": sliced,
        "meta": {
            "mode": "fast",
            "orig_chars": len(text),
            "sliced_chars": len(sliced),
            "orig_lines": len(lines),
            "sliced_lines": len(picked),
        },
    }


def _clip_text_for_llm(text: str) -> tuple[str, Dict[str, Any]]:
    max_chars = _env_int("QWEN_INPUT_MAX_CHARS", 80000)
    if max_chars <= 0 or len(text) <= max_chars:
        return text, {"llm_input_chars": len(text), "llm_clipped": False}

    head_ratio_raw = os.environ.get("QWEN_INPUT_HEAD_RATIO", "0.75")
    try:
        head_ratio = float(head_ratio_raw)
    except Exception:
        head_ratio = 0.75
    head_ratio = min(0.95, max(0.5, head_ratio))

    marker = "\n\n...[TRUNCATED_FOR_SPEED]...\n\n"
    head_chars = int(max_chars * head_ratio)
    tail_chars = max(0, max_chars - head_chars - len(marker))
    if tail_chars > 0:
        clipped = text[:head_chars] + marker + text[-tail_chars:]
    else:
        clipped = text[:max_chars]

    return clipped, {"llm_input_chars": len(clipped), "llm_clipped": True, "llm_orig_chars": len(text)}


# =========================
# LLM with timeout
# =========================
def _llm_with_timeout(text: str, timeout_s: int) -> tuple[dict, Dict[str, Any]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(review_contract, text)
        return fut.result(timeout=timeout_s)


# =========================
# accurate pipeline (MinerU)
# =========================
def _run_accurate(job_id: int, pdf_path: str, out_dir: Path) -> Dict[str, Any]:
    notify_django({"job_id": job_id, "status": "running", "progress": 35, "stage": "mineru_start", "mode": "accurate"})
    try:
        run_mineru_cli(pdf_path=pdf_path, out_dir=str(out_dir))
    except Exception as e:
        cur_device = _sanitize_mineru_device(os.environ.get("MINERU_DEVICE", ""))
        if cur_device != "cpu" and _is_cuda_failure(e):
            print(f"[job {job_id}] mineru GPU failed, retry CPU: {e}", flush=True)
            notify_django({"job_id": job_id, "status": "running", "progress": 45, "stage": "mineru_retry_cpu", "mode": "accurate"})
            run_mineru_cli(pdf_path=pdf_path, out_dir=str(out_dir), force_device="cpu")
        else:
            raise
    notify_django({"job_id": job_id, "status": "running", "progress": 65, "stage": "mineru_done", "mode": "accurate"})

    md_path = _find_largest_md(out_dir)
    print(f"[job {job_id}] md_path={md_path}", flush=True)
    if not md_path or not md_path.exists():
        notify_django(
            {
                "job_id": job_id,
                "status": "running",
                "progress": 68,
                "stage": "mineru_no_md_fallback",
                "mode": "accurate",
                "meta": {"reason": "no_markdown"},
            }
        )
        fallback_dir = out_dir / "accurate_fallback_ocr"
        text = _fast_ocr_pdf_to_text(pdf_path, fallback_dir)
        if not text.strip():
            raise RuntimeError(f"no markdown found in {out_dir}; fallback OCR produced empty text")
        sliced = _fast_slice_text(text)
        final_text = (sliced.get("text") or "").strip() or text.strip()
        quality = _ocr_quality_metrics(text)
        meta = {
            "mode": "accurate_fallback",
            "fallback_reason": "mineru_no_markdown",
            "orig_chars": len(text),
            "fast_ocr_score": quality["score"],
            "fast_keyword_hits": quality["keyword_hits"],
            "fast_garbage_ratio": quality["garbage_ratio"],
        }
        if isinstance(sliced.get("meta"), dict):
            for k, v in sliced["meta"].items():
                if k != "mode":
                    meta[k] = v
        notify_django(
            {
                "job_id": job_id,
                "status": "running",
                "progress": 72,
                "stage": "accurate_fallback_done",
                "mode": "accurate_fallback",
                "meta": {"fallback_text_chars": len(final_text), "fast_ocr_score": quality["score"]},
            }
        )
        return {"text": final_text, "meta": meta}

    md_text = md_path.read_text(encoding="utf-8", errors="ignore")
    md_text = _strip_md_images(md_text)
    if not md_text.strip():
        notify_django(
            {
                "job_id": job_id,
                "status": "running",
                "progress": 68,
                "stage": "mineru_empty_md_fallback",
                "mode": "accurate",
            }
        )
        fallback_dir = out_dir / "accurate_fallback_ocr"
        text = _fast_ocr_pdf_to_text(pdf_path, fallback_dir)
        if not text.strip():
            raise RuntimeError(f"mineru markdown empty in {out_dir}; fallback OCR produced empty text")
        sliced = _fast_slice_text(text)
        final_text = (sliced.get("text") or "").strip() or text.strip()
        quality = _ocr_quality_metrics(text)
        meta = {
            "mode": "accurate_fallback",
            "fallback_reason": "mineru_empty_markdown",
            "orig_chars": len(text),
            "fast_ocr_score": quality["score"],
            "fast_keyword_hits": quality["keyword_hits"],
            "fast_garbage_ratio": quality["garbage_ratio"],
        }
        if isinstance(sliced.get("meta"), dict):
            for k, v in sliced["meta"].items():
                if k != "mode":
                    meta[k] = v
        notify_django(
            {
                "job_id": job_id,
                "status": "running",
                "progress": 72,
                "stage": "accurate_fallback_done",
                "mode": "accurate_fallback",
                "meta": {"fallback_text_chars": len(final_text), "fast_ocr_score": quality["score"]},
            }
        )
        return {"text": final_text, "meta": meta}
    return {"text": md_text, "meta": {"mode": "accurate", "orig_chars": len(md_text)}}


# =========================
# main pipeline
# =========================
def _do_analyze(job_id: int, pdf_path: str, out_root: str):
    mode = _review_mode()

    try:
        out_root = out_root or str(BASE_DIR / "worker_out")
        out_dir = Path(out_root).resolve() / f"job_{job_id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        page_count = _pdf_page_count(pdf_path)
        fast_only_pages = int(os.environ.get("FAST_ONLY_IF_PAGES_GT") or "0")

        # stamp detection (YOLO) - full doc with early-exit
        stamp_result: Optional[Dict[str, Any]] = None
        stamp_images: Optional[List[Path]] = None
        ocr_indices: Optional[List[int]] = None
        stamp_enabled = _env_flag("STAMP_ENABLED", False)
        stamp_full_doc = _env_flag("STAMP_FULL_DOC", True)
        stamp_skip_pages = _env_int("STAMP_SKIP_IF_PAGES_GT", 0)
        stamp_model = (os.environ.get("STAMP_YOLO_MODEL_PATH") or "").strip().strip('"').strip("'")
        ocr_max_pages = _env_int("OCR_MAX_PAGES", 0)
        ocr_head_pages = _env_int("OCR_HEAD_PAGES", 6)
        ocr_tail_pages = _env_int("OCR_TAIL_PAGES", 2)

        if page_count > 0 and ocr_max_pages > 0:
            ocr_indices = _select_page_indices(page_count, ocr_max_pages, ocr_head_pages, ocr_tail_pages)

        if stamp_skip_pages > 0 and page_count > stamp_skip_pages:
            stamp_result = {
                "stamp_status": "UNCERTAIN",
                "evidence": [],
                "stamp_skipped": True,
                "stamp_skip_reason": f"pages={page_count} > limit={stamp_skip_pages}",
            }
        elif stamp_enabled and stamp_model and Path(stamp_model).exists():
            try:
                stamp_dpi = _env_int("STAMP_DPI", _env_int("OCR_DPI", 200))
                stamp_max_pages = _env_int("STAMP_MAX_PAGES", 0)
                stamp_head_pages = _env_int("STAMP_HEAD_PAGES", ocr_head_pages)
                stamp_tail_pages = _env_int("STAMP_TAIL_PAGES", ocr_tail_pages)
                pages_dir = out_dir / ("pages_full" if stamp_full_doc else "pages_sampled")
                render_pdf = pdf_path
                # If full-doc is disabled or max pages is configured, sample pages first.
                if (not stamp_full_doc) or (stamp_max_pages > 0):
                    sample_cap = stamp_max_pages if stamp_max_pages > 0 else ocr_max_pages
                    if sample_cap > 0 and page_count > 0:
                        sampled_pdf = pages_dir / "stamp_sampled.pdf"
                        render_pdf, _ = _sample_pdf_for_render(
                            pdf_path=pdf_path,
                            out_pdf=sampled_pdf,
                            max_pages=sample_cap,
                            head_pages=stamp_head_pages,
                            tail_pages=stamp_tail_pages,
                        )

                stamp_images = _render_pdf_pages(render_pdf, pages_dir, stamp_dpi, gray=False)

                if stamp_images:
                    notify_django({"job_id": job_id, "status": "running", "progress": 12, "stage": "stamp_start", "mode": mode})
                    stamp_result = _detect_stamp_yolo_subprocess(
                        page_images=stamp_images,
                        work_dir=out_dir / "stamp_subprocess",
                    )
                    notify_django({"job_id": job_id, "status": "running", "progress": 14, "stage": "stamp_done", "mode": mode})
            except Exception as e:
                stamp_result = {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": str(e)}

        # Fallback: simple red-stamp detection from PDF if YOLO is missing/uncertain/no
        fallback_red = (os.environ.get("STAMP_FALLBACK_RED") or "1").strip() in {"1", "true", "yes", "y", "on"}
        if fallback_red:
            try:
                need_fallback = stamp_result is None or stamp_result.get("stamp_status") in {"NO", "UNCERTAIN"}
                if need_fallback:
                    from contract_review.services.stamp_detect import detect_stamp_status_from_pdf  # type: ignore

                    max_pages = int(os.environ.get("STAMP_FALLBACK_PAGES") or "8")
                    tail_pages = int(os.environ.get("STAMP_FALLBACK_TAIL") or "4")
                    red = detect_stamp_status_from_pdf(pdf_path, max_pages=max_pages, tail_pages=tail_pages)
                    if isinstance(red, dict):
                        red["stamp_method"] = "red_fallback"
                        if stamp_result is None:
                            stamp_result = red
                        else:
                            if red.get("stamp_status") in {"YES", "NO", "UNCERTAIN"}:
                                # prefer red result when YOLO is uncertain or negative
                                if stamp_result.get("stamp_status") != "YES":
                                    stamp_result.update(red)
            except Exception as e:
                if stamp_result is None:
                    stamp_result = {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_fallback_error": str(e)}
                else:
                    stamp_result["stamp_fallback_error"] = str(e)
        if fast_only_pages > 0 and page_count > fast_only_pages and mode in ("auto", "accurate"):
            print(f"[job {job_id}] force fast (pages={page_count} > {fast_only_pages})", flush=True)
            mode = "fast"

        notify_django({"job_id": job_id, "status": "running", "progress": 5, "stage": "start", "mode": mode, "meta": {"pages": page_count}})

        timeout_s = int(os.environ.get("QWEN_TIMEOUT") or "180")
        fallback_min_chars = _env_int("AUTO_FALLBACK_MIN_CHARS", 200)
        fallback_min_score = _env_float("AUTO_FALLBACK_MIN_OCR_SCORE", 0.58)
        force_accurate = (os.environ.get("AUTO_FORCE_ACCURATE") or "0").strip() == "1"
        if fast_only_pages > 0 and page_count > fast_only_pages:
            force_accurate = False

        final_text = ""
        meta: Dict[str, Any] = {"mode": mode}

        if mode in ("auto", "fast"):
            notify_django({"job_id": job_id, "status": "running", "progress": 15, "stage": "ocr_start", "mode": "fast"})
            ocr_dir = out_dir / "fast_ocr"
            text = _fast_ocr_pdf_to_text(pdf_path, ocr_dir, page_images=stamp_images, page_indices=ocr_indices)
            stamp_images = None
            fast_quality = _ocr_quality_metrics(text)

            notify_django({
                "job_id": job_id,
                "status": "running",
                "progress": 28,
                "stage": "ocr_done",
                "mode": "fast",
                "meta": {
                    "fast_chars": len(text),
                    "fast_ocr_score": fast_quality["score"],
                    "fast_keyword_hits": fast_quality["keyword_hits"],
                    "fast_garbage_ratio": fast_quality["garbage_ratio"],
                },
            })

            if mode == "fast":
                sliced = _fast_slice_text(text)
                final_text = sliced["text"]
                meta = sliced["meta"]
                meta.update({
                    "fast_ocr_score": fast_quality["score"],
                    "fast_keyword_hits": fast_quality["keyword_hits"],
                    "fast_garbage_ratio": fast_quality["garbage_ratio"],
                })
            else:
                fallback_reasons: List[str] = []
                if force_accurate:
                    fallback_reasons.append("force_accurate")
                if len(text.strip()) < fallback_min_chars:
                    fallback_reasons.append("chars_below_threshold")
                if fast_quality["score"] < fallback_min_score:
                    fallback_reasons.append("score_below_threshold")

                if fallback_reasons:
                    notify_django({
                        "job_id": job_id,
                        "status": "running",
                        "progress": 30,
                        "stage": "fallback_to_accurate",
                        "mode": "auto",
                        "meta": {
                            "fast_chars": len(text),
                            "fast_ocr_score": fast_quality["score"],
                            "min_chars": fallback_min_chars,
                            "min_score": fallback_min_score,
                            "force": force_accurate,
                            "reasons": fallback_reasons,
                        },
                    })
                    acc = _run_accurate(job_id, pdf_path, out_dir)
                    final_text = acc["text"]
                    meta = acc["meta"]
                    meta.update({
                        "fast_probe_chars": len(text),
                        "fast_probe_score": fast_quality["score"],
                        "fallback_reasons": fallback_reasons,
                    })
                else:
                    sliced = _fast_slice_text(text)
                    final_text = sliced["text"]
                    meta = sliced["meta"]
                    meta.update({
                        "fast_ocr_score": fast_quality["score"],
                        "fast_keyword_hits": fast_quality["keyword_hits"],
                        "fast_garbage_ratio": fast_quality["garbage_ratio"],
                    })

        elif mode == "accurate":
            acc = _run_accurate(job_id, pdf_path, out_dir)
            final_text = acc["text"]
            meta = acc["meta"]

        else:
            # unknown -> auto
            notify_django({"job_id": job_id, "status": "running", "progress": 15, "stage": "ocr_start", "mode": "fast"})
            ocr_dir = out_dir / "fast_ocr"
            text = _fast_ocr_pdf_to_text(pdf_path, ocr_dir, page_images=stamp_images, page_indices=ocr_indices)
            stamp_images = None
            fast_quality = _ocr_quality_metrics(text)
            notify_django({
                "job_id": job_id,
                "status": "running",
                "progress": 28,
                "stage": "ocr_done",
                "mode": "fast",
                "meta": {
                    "fast_chars": len(text),
                    "fast_ocr_score": fast_quality["score"],
                    "fast_keyword_hits": fast_quality["keyword_hits"],
                    "fast_garbage_ratio": fast_quality["garbage_ratio"],
                },
            })

            fallback_reasons = []
            if len(text.strip()) < fallback_min_chars:
                fallback_reasons.append("chars_below_threshold")
            if fast_quality["score"] < fallback_min_score:
                fallback_reasons.append("score_below_threshold")

            if fallback_reasons:
                notify_django({
                    "job_id": job_id,
                    "status": "running",
                    "progress": 30,
                    "stage": "fallback_to_accurate",
                    "mode": "auto",
                    "meta": {
                        "fast_chars": len(text),
                        "fast_ocr_score": fast_quality["score"],
                        "min_chars": fallback_min_chars,
                        "min_score": fallback_min_score,
                        "reasons": fallback_reasons,
                    },
                })
                acc = _run_accurate(job_id, pdf_path, out_dir)
                final_text = acc["text"]
                meta = acc["meta"]
                meta.update({
                    "fast_probe_chars": len(text),
                    "fast_probe_score": fast_quality["score"],
                    "fallback_reasons": fallback_reasons,
                })
            else:
                sliced = _fast_slice_text(text)
                final_text = sliced["text"]
                meta = sliced["meta"]
                meta.update({
                    "fast_ocr_score": fast_quality["score"],
                    "fast_keyword_hits": fast_quality["keyword_hits"],
                    "fast_garbage_ratio": fast_quality["garbage_ratio"],
                })

        if _should_run_llm_ocr_fix(final_text):
            notify_django(
                {
                    "job_id": job_id,
                    "status": "running",
                    "progress": 74,
                    "stage": "ocr_llm_fix_start",
                    "mode": meta.get("mode"),
                }
            )
            try:
                fixed_text, fix_meta = fix_ocr_text(final_text)
                if fixed_text and len(fixed_text.strip()) >= max(120, int(len(final_text.strip()) * 0.45)):
                    final_text = _normalize_ocr_text(fixed_text)
                    meta["ocr_llm_fix"] = {"applied": True, "chars": len(final_text), "llm": fix_meta}
                else:
                    meta["ocr_llm_fix"] = {"applied": False, "reason": "too_short_or_empty", "llm": fix_meta}
            except Exception as e:
                meta["ocr_llm_fix"] = {"applied": False, "error": str(e)}
            notify_django(
                {
                    "job_id": job_id,
                    "status": "running",
                    "progress": 77,
                    "stage": "ocr_llm_fix_done",
                    "mode": meta.get("mode"),
                    "meta": meta.get("ocr_llm_fix"),
                }
            )

        if _env_flag("OCR_ZERO_TOLERANCE", False):
            guard = _ocr_zero_tolerance_guard(final_text)
            meta["ocr_zero_tolerance"] = guard
            if not guard.get("ok", False):
                err = (
                    "OCR zero-tolerance guard blocked result: "
                    f"score={guard.get('score')} reasons={','.join(guard.get('reasons') or [])} "
                    f"bad_terms={guard.get('bad_terms') or []}"
                )
                result_json = build_error_result(err, mode=meta.get("mode"), meta=meta, stamp_result=stamp_result)
                notify_django(
                    {
                        "job_id": job_id,
                        "status": "error",
                        "progress": 100,
                        "stage": "ocr_zero_guard_failed",
                        "error": err,
                        "mode": meta.get("mode"),
                        "meta": meta,
                        "result_markdown": _clip_markdown_for_callback(final_text, is_error=True),
                        "result_json": result_json,
                    }
                )
                return

        llm_text, llm_meta = _clip_text_for_llm(final_text)
        meta.update(llm_meta)
        notify_django({"job_id": job_id, "status": "running", "progress": 80, "stage": "llm_start", "mode": meta.get("mode"), "meta": meta})

        try:
            review_json, llm_call_meta = _llm_with_timeout(llm_text, timeout_s)
            meta["llm_call"] = llm_call_meta
            if isinstance(review_json, dict):
                review_json["_llm_meta"] = llm_call_meta
        except concurrent.futures.TimeoutError:
            err = f"llm timeout after {timeout_s}s"
            result_json = build_error_result(err, mode=meta.get("mode"), meta=meta, stamp_result=stamp_result)
            notify_django({
                "job_id": job_id,
                "status": "error",
                "progress": 100,
                "stage": "llm_timeout",
                "error": err,
                "mode": meta.get("mode"),
                "meta": meta,
                "result_markdown": _clip_markdown_for_callback(final_text, is_error=True),
                "result_json": result_json,
            })
            return
        except Exception as e:
            err = f"llm failed: {e}"
            result_json = build_error_result(err, mode=meta.get("mode"), meta=meta, stamp_result=stamp_result)
            notify_django({
                "job_id": job_id,
                "status": "error",
                "progress": 100,
                "stage": "llm_error",
                "error": err,
                "mode": meta.get("mode"),
                "meta": meta,
                "result_markdown": _clip_markdown_for_callback(final_text, is_error=True),
                "result_json": result_json,
            })
            return

        review_json = merge_stamp_result(review_json, stamp_result)

        notify_django({
            "job_id": job_id,
            "status": "done",
            "progress": 100,
            "stage": "done",
            "mode": meta.get("mode"),
            "meta": meta,
            "result_markdown": _clip_markdown_for_callback(final_text, is_error=False),
            "result_json": review_json,
        })
        print(f"[job {job_id}] finished | mode={meta.get('mode')}", flush=True)

    except Exception as e:
        err = f"worker exception: {e}"
        print(f"[job {job_id}] FATAL: {err}", flush=True)
        notify_django({"job_id": job_id, "status": "error", "progress": 100, "stage": "worker_error", "error": err, "mode": mode})


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    try:
        task = celery_app.send_task(
            "contract_review_worker.analyze_job",
            args=[req.job_id, req.pdf_path, req.out_root],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"enqueue failed: {e}")
    return {"ok": True, "job_id": req.job_id, "mode": _review_mode(), "task_id": task.id}
