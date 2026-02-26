from django.contrib import admin
from django.urls import path,include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    # 访问根路径 / 自动跳转到 /contract/
    path("", RedirectView.as_view(url="/contract/api/health/", permanent=False)),

    path("admin/", admin.site.urls),
    path("contract/", include("contract_review.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
