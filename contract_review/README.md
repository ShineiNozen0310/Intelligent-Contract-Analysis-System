# Django 业务层（`contract_review`）

`contract_review` 是 Django 业务接口层，负责：

- 接收上传 PDF，创建审查任务
- 查询任务状态
- 接收 Worker 回调并落库
- 生成审查报告并导出 PDF

## 1. API 列表

所有路由前缀：`/contract/`

- `GET /contract/api/health/`
  - 健康检查
- `POST /contract/api/start/`
  - 上传 PDF 并创建任务
  - `form-data` 字段：`file`
- `GET /contract/api/status/{job_id}/`
  - 查询任务状态与结果
- `POST /contract/api/job/update/`
  - Worker 回调写入结果
- `GET /contract/api/export_pdf/{job_id}/`
  - 导出 PDF 报告

## 2. 任务数据模型

模型：`models.py / ContractJob`

- `status`：`queued | running | done | error`
- `progress`：进度百分比
- `stage`：阶段标识（如 `ocr_start`、`llm_start`）
- `filename`：上传文件名
- `file_sha256`：输入文件哈希
- `result_markdown`：报告文本
- `result_json`：结构化结果
- `error`：失败原因

## 3. 请求流转

### 3.1 创建任务

`start_analyze` 流程：

1. 校验上传文件是否为 PDF
2. 创建 `ContractJob`
3. 存储文件到 `MEDIA_ROOT/job_{id}_{sha}/input.pdf`
4. 调用 Worker `/analyze` 提交异步任务

### 3.2 状态查询

`job_status` 会按任务状态返回内容：

- `done`：返回 `result_json` 和 `result_markdown`
- `error`：返回 `error` 及可能已有的 `result_json`

### 3.3 Worker 回调

`job_update` 接收 JSON：

- `job_id`
- `status`
- `progress`
- `stage`
- `result_markdown`
- `result_json`
- `error`

并做容错处理（非法 JSON、字段缺失、超长 markdown 截断等）。

## 4. 报告导出

`export_pdf` 采用双引擎策略：

1. 优先：`wkhtmltopdf`（HTML 渲染）
2. 回退：`reportlab`（基础排版）

关键函数在 `views.py`：

- `_build_review_html`
- `_build_review_markdown`
- `_build_pdf_with_reportlab`

## 5. 配置项（与本模块直接相关）

来自 `DjangoProject1/settings.py`：

- `WORKER_TOKEN`
- `WORKER_TIMEOUT`
- `WORKER_SUBMIT_RETRY`
- `MAX_RESULT_MARKDOWN_CHARS`
- `JOB_RETENTION_DAYS`

## 6. 开发检查

```bat
.\.venv\Scripts\python.exe manage.py check
```

可配合接口联调：

```bat
start_all.bat start
```
