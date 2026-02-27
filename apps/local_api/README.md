# Local API（迁移目标）

`apps/local_api` 提供本地统一 API，默认端口 `8003`。

- 主实现：`apps/local_api/main.py`
- 上游依赖：`http://127.0.0.1:8000/contract`

## 当前定位

- 业务端点仍由 Django（`contract_review`）实现。
- Local API 负责代理与统一返回结构。
- 报告协议通过 `packages/shared_contract_schema` 统一。

## 运行

```bat
.\.venv\Scripts\python.exe -m uvicorn apps.local_api.main:app --host 127.0.0.1 --port 8003
```

## 接口

- `GET /contract/api/health/`
- `POST /contract/api/start/`
- `GET /contract/api/status/{job_id}/`
- `GET /contract/api/result/{job_id}/`
- `GET /contract/api/export_pdf/{job_id}/`
