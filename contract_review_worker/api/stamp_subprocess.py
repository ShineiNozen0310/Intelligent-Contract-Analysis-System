from __future__ import annotations

import argparse
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from contract_review_worker.app_config import bootstrap

BASE_DIR = Path(__file__).resolve().parents[2]
bootstrap(BASE_DIR)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    if not _env_flag("STAMP_ENABLED", False):
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": "STAMP_ENABLED=0"}

    local_path = (os.environ.get("STAMP_YOLO_MODEL_PATH") or "").strip().strip('"').strip("'")
    repo = (os.environ.get("STAMP_HF_REPO") or "stamps-labs/yolo-stamp").strip()
    filename = (os.environ.get("STAMP_HF_FILENAME") or "weights.pt").strip()
    if local_path and not Path(local_path).exists():
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": f"model not found: {local_path}"}

    early_exit = _env_flag("STAMP_EARLY_EXIT", True)
    device = _normalize_device(os.environ.get("STAMP_DEVICE") or "")
    if not device:
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    try:
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
    if not _env_flag("STAMP_ENABLED", False):
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": "STAMP_ENABLED=0"}

    model_path = (os.environ.get("STAMP_YOLO_MODEL_PATH") or "").strip().strip('"').strip("'")
    if not model_path:
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": "STAMP_YOLO_MODEL_PATH missing"}
    if not Path(model_path).exists():
        return {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": f"model not found: {model_path}"}

    conf = float(os.environ.get("STAMP_YOLO_CONF") or "0.25")
    imgsz = int(os.environ.get("STAMP_IMG_SIZE") or "1024")
    early_exit = _env_flag("STAMP_EARLY_EXIT", True)

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


def _load_request_images(request_path: Path) -> List[Path]:
    raw = request_path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    paths = data.get("images") if isinstance(data, dict) else []
    if not isinstance(paths, list):
        return []
    out: List[Path] = []
    for p in paths:
        if isinstance(p, str) and p.strip():
            fp = Path(p).resolve()
            if fp.exists():
                out.append(fp)
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run stamp detection in isolated subprocess.")
    parser.add_argument("--request", required=True, help="Path to JSON request file.")
    parser.add_argument("--output", required=True, help="Path to JSON output file.")
    args = parser.parse_args(argv)

    request_path = Path(args.request).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Any]
    try:
        images = _load_request_images(request_path)
        result = _detect_stamp_yolo(images)
    except Exception as e:
        result = {"stamp_status": "UNCERTAIN", "evidence": [], "stamp_error": f"stamp subprocess exception: {e}"}

    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
