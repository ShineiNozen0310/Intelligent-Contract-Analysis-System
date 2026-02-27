# Django 业务层（contract_review）

`contract_review` 负责任务创建、状态查询、Worker 回调与 PDF 导出。

## API

- `GET /contract/api/health/`
- `POST /contract/api/start/`
- `GET /contract/api/status/{job_id}/`
- `GET /contract/api/result/{job_id}/`
- `POST /contract/api/job/update/`
- `GET /contract/api/export_pdf/{job_id}/`

## 核心模型

`ContractJob` 关键字段：

- `status`（queued/running/done/error）
- `progress`
- `stage`
- `result_markdown`
- `result_json`
- `error`

## 导出策略

- 优先 `wkhtmltopdf`
- 回退 `reportlab`

## 自检

```bat
.\.venv\Scripts\python.exe manage.py check
```
