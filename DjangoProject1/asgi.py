import os
from django.core.asgi import get_asgi_application
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

# 先初始化 Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'DjangoProject1.settings')
django_asgi_app = get_asgi_application()

# 创建 FastAPI 实例
app = FastAPI()

# FastAPI 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境临时开启
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 Django 的 ASGI 应用（Django 路由仍可用）
app.mount("/django", django_asgi_app)

# 挂载媒体文件（FastAPI 可访问上传的 PDF）
from django.conf import settings

app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")

# FastAPI 核心接口：PDF 合同分析
from contract_review.review import review_contract  # 导入 MinerU 分析逻辑
from fastapi import UploadFile, File
import os


@app.post("/analyze")
async def analyze_contract(file: UploadFile = File(...)):
    """FastAPI 接口：上传 PDF 并返回分析结果"""
    # 保存上传的 PDF 文件
    pdf_path = os.path.join(settings.MEDIA_ROOT, file.filename)
    with open(pdf_path, "wb") as f:
        f.write(await file.read())

    # 调用 MinerU 分析逻辑
    try:
        result = review_contract(pdf_path)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        # 清理临时文件
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


# 启动命令（后续可通过此命令同时运行 Django + FastAPI）
# uvicorn DjangoProject1.asgi:app --reload --port 8000

# 保留 Django 原有 ASGI 入口（兼容原有启动方式）
application = app