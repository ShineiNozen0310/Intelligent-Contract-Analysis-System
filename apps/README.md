# Apps 目录说明

工程采用 Flutter 优先的客户端结构：

- `apps/local_api`：FastAPI 本地统一 API
- `apps/mobile_client_flutter`：Flutter 客户端（Windows/Android/iOS）

后端核心服务在：

- `contract_review`（Django API）
- `contract_review_worker`（FastAPI + Celery Worker）

## 启动（PowerShell）

```bat
.\start_all.bat start
.\run_flutter_client.bat
```

或一键启动：

```bat
.\launch_flutter_oneclick.bat
```

查看状态：

```bat
.\start_all.bat status
```
