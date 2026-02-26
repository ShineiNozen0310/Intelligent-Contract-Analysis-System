from django.urls import path
from . import views

urlpatterns = [
    path("api/health/", views.api_health, name="contract_api_health"),
    path("api/start/", views.start_analyze, name="contract_api_start"),
    path("api/status/<int:job_id>/", views.job_status, name="contract_api_status"),
    path("api/job/update/", views.job_update, name="contract_api_job_update"),
    path("api/export_pdf/<int:job_id>/", views.export_pdf, name="contract_api_export_pdf"),

]
