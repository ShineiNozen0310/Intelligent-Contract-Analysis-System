# Django 配置层（DjangoProject1）

`DjangoProject1` 负责全局配置与根路由。

## 文件职责

- `settings.py`：环境变量、应用、数据库、全局参数
- `urls.py`：根路由分发（挂载 `contract_review.urls`）
- `wsgi.py` / `asgi.py`：部署入口

## 主要路由

- `/` -> `/contract/api/health/`
- `/admin/`
- `/contract/`

## 常用配置

- `SECRET_KEY`
- `DEBUG`
- `WORKER_TOKEN`
- `WORKER_TIMEOUT`
- `MAX_RESULT_MARKDOWN_CHARS`
- `JOB_RETENTION_DAYS`

## 本地检查

```bat
.\.venv\Scripts\python.exe manage.py check
```
