from django.db import models

class ContractJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "queued"),
        ("running", "running"),
        ("done", "done"),
        ("error", "error"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued")
    progress = models.IntegerField(default=0)
    stage = models.CharField(max_length=64, default="queued")

    file_sha256 = models.CharField(max_length=64, db_index=True, default="")
    filename = models.CharField(max_length=255, default="")

    result_markdown = models.TextField(blank=True, default="")
    result_json = models.JSONField(blank=True, null=True)
    runtime_meta = models.JSONField(blank=True, null=True, default=dict)
    error = models.TextField(blank=True, default="")
