# Django 工程配置层（`DjangoProject1`）

`DjangoProject1` 是 Django 项目配置目录，负责全局 settings 与路由装配。

## 1. 目录职责

- `settings.py`：环境变量加载、应用注册、数据库与全局参数
- `urls.py`：根路由分发（挂载 `contract_review.urls`）
- `wsgi.py` / `asgi.py`：部署入口
- `__init__.py`：包初始化

## 2. 路由结构

`urls.py` 主要规则：

- `/` 重定向到 `/contract/api/health/`
- `/admin/` Django 管理后台
- `/contract/` 挂载业务模块 `contract_review`

开发模式下会额外挂载 `MEDIA_URL` 静态访问。

## 3. 配置加载顺序

项目启动时会优先读取根目录 `.env`（如果存在），并覆盖系统环境变量。

常见配置：

- `SECRET_KEY`
- `DEBUG`
- `WKHTMLTOPDF_BIN`
- `WORKER_TOKEN`
- `WORKER_TIMEOUT`
- `WORKER_SUBMIT_RETRY`
- `MAX_RESULT_MARKDOWN_CHARS`
- `JOB_RETENTION_DAYS`

## 4. 默认基础配置

- 数据库：SQLite（`db.sqlite3`）
- 时区：`Asia/Shanghai`
- 语言：`zh-hans`
- `ALLOWED_HOSTS`：`127.0.0.1` / `localhost`

## 5. 本地检查

```bat
.\.venv\Scripts\python.exe manage.py check
```

如果出现导出 PDF 相关错误，可优先检查：

- `WKHTMLTOPDF_BIN` 指向是否有效
- `.venv/wkhtmltopdf/bin/wkhtmltopdf.exe` 是否存在（或系统已安装）
