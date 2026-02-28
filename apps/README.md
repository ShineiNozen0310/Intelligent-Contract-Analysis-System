# Apps 目录说明

工程采用 Flutter 优先的客户端结构：

- `apps/local_api`：FastAPI 本地统一 API（前端只连 8003）
- `apps/mobile_client_flutter`：Flutter 客户端（Windows / Android / iOS / Web）

后端核心服务：

- `contract_review`（Django API，8000）
- `contract_review_worker`（FastAPI + Celery，8001）

## 推荐启动方式

### 业务用户（Release）

```bat
.\launch_flutter_release_oneclick.bat
```

### 开发调试（一键）

```bat
.\launch_flutter_oneclick.bat
```

### 手动指定 Flutter 设备

```bat
.\run_flutter_client.bat windows
.\run_flutter_client.bat android --debug
```

### 查看后端状态

```bat
.\start_all.bat status
```

## 脚本分层说明

- 核心脚本位于 `scripts/dev`、`scripts/release`、`scripts/ops`。
- 根目录脚本为兼容入口，历史命令可继续使用。
