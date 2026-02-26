from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from contract_review.models import ContractJob


class Command(BaseCommand):
    help = "Delete old ContractJob records and their media folders."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Retain jobs for N days (default: settings.JOB_RETENTION_DAYS or 30).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only print what would be deleted.",
        )

    def handle(self, *args, **options):
        days = options.get("days")
        if days is None:
            days = int(getattr(settings, "JOB_RETENTION_DAYS", 30))

        if days <= 0:
            self.stdout.write(self.style.WARNING("JOB_RETENTION_DAYS <= 0, nothing to cleanup."))
            return

        cutoff = timezone.now() - timedelta(days=days)
        qs = ContractJob.objects.filter(created_at__lt=cutoff)

        media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
        dry_run = bool(options.get("dry_run"))

        count = 0
        for job in qs.iterator():
            count += 1
            patterns = [f"job_{job.id}", f"job_{job.id}_*"]
            for pat in patterns:
                for p in media_root.glob(pat):
                    if p.is_dir():
                        if dry_run:
                            self.stdout.write(f"[dry-run] remove {p}")
                        else:
                            shutil.rmtree(p, ignore_errors=True)

        if not dry_run:
            qs.delete()

        self.stdout.write(self.style.SUCCESS(f"cleanup done. affected jobs: {count}"))
