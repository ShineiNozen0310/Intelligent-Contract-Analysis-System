from __future__ import annotations

from contract_review_worker.celery_app import app


@app.task(name="contract_review_worker.analyze_job")
def analyze_job(job_id: int, pdf_path: str, out_root: str = "") -> None:
    # Lazy import to avoid circular imports at worker startup
    from contract_review_worker.api.main import _do_analyze

    _do_analyze(job_id, pdf_path, out_root)
