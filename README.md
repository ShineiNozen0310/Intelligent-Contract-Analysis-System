# 智能合同审查系统

面向企业法务、采购、合同管理场景的本地化审查产品。系统以“上传合同 -> 自动审查 -> 输出结论 -> 导出报告”为核心闭环，帮助业务团队在更短时间内识别风险、沉淀审查依据并形成可复核文档。

## 产品价值

- 降本增效：减少人工逐页通读工作量，快速定位关键风险点。
- 统一口径：输出结构化审查结论，降低不同审查人之间的口径差异。
- 结果可追溯：保留任务状态、阶段耗时、错误码与诊断信息，便于复核与审计。
- 本地部署：支持私有化运行，兼顾数据安全与可控性。

## 核心能力

- 合同 PDF 审查流程
  - 上传 PDF
  - OCR 文本提取
  - 合同信息与风险识别
  - 审查建议生成
  - 报告导出（PDF）
- 健康检查与启动诊断
  - 一键检查 Django / Worker / Redis / vLLM 可用性
  - 返回标准错误码与修复建议
- 稳定运行机制
  - 后端服务统一由启动脚本拉起
  - Django 启动前自动执行迁移（避免表结构不一致导致运行异常）
- 更新能力
  - 提供更新清单检查接口
  - 支持版本号与安装包校验信息管理

## 技术架构

```text
Flutter 客户端 (Windows 桌面)
  -> Local API (8003)
      -> Django API (8000)
      -> Worker API (8001)
          -> Celery + Redis
          -> OCR / 风险识别 / 报告构建
          -> vLLM (8002，可选或必选，取决于配置)
```

默认端口：

- `8000` Django
- `8001` Worker
- `8002` vLLM
- `8003` Local API（前端统一入口）

## 目录结构

```text
apps/
  local_api/                 # 前端统一 API（健康检查、任务代理、更新检查）
  mobile_client_flutter/     # Flutter 桌面端（后续可扩展移动端）
contract_review/             # Django 业务接口
contract_review_worker/      # 异步审查执行与回调
DjangoProject1/              # Django 项目配置
packages/                    # 共享领域模块/结构化 schema
scripts/
  ops/                       # 启停、状态检查、运维脚本
  release/                   # 打包、安装器、更新清单脚本
hf_models/                   # 本地模型（通常不入库）
runtime/                     # 运行日志与发布产物（通常不入库）
```

## 快速开始（业务使用）

### 1. 启动系统

在工程根目录执行：

```bat
start_all.bat start
```

查看状态：

```bat
start_all.bat status
```

### 2. 启动客户端

开发调试：

```bat
run_flutter_client.bat windows
```

或使用一键启动脚本：

```bat
launch_flutter_release_oneclick.bat
```

### 3. 执行审查

- 点击“健康检查”确认服务可用
- 上传 PDF，点击“开始审查”
- 审查完成后查看报告并导出 PDF

## 开发环境准备

### Python 依赖

```bat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### Flutter 依赖

```bat
cd apps\mobile_client_flutter
..\..\tools\flutter\bin\flutter.bat pub get
```

### 基础检查

```bat
.\.venv\Scripts\python.exe manage.py check
..\tools\flutter\bin\flutter.bat analyze
```

## 关键配置说明（.env）

建议先复制模板：

```bat
copy .env.example .env
```

常用配置项：

- 服务与路由
  - `DJANGO_HOST` / `DJANGO_PORT`
  - `LOCAL_API_DJANGO_BASE`
  - `WORKER_BASE_URL`
- LLM 路由
  - `LLM_PROVIDER`
  - `LLM_PRIMARY_PROVIDER`
  - `LLM_REQUIRE_LOCAL_VLLM`
  - `LOCAL_VLLM_BASE_URL`
  - `LOCAL_VLLM_API_KEY`
- 启动与稳定性
  - `DJANGO_SERVER_MODE=waitress`（推荐）
  - `RUN_DJANGO_MIGRATE_ON_START=1`（启动自动迁移）

## 打包与发布

### Flutter Release

```bat
build_flutter_release.bat
```

产物：

```text
apps/mobile_client_flutter/build/windows/x64/runner/Release/contract_review_flutter.exe
```

### 安装包构建

```bat
build_installer_inno.bat
```

或（NSIS）：

```bat
build_installer_nsis.bat
```

> 完整包（含 `.venv` + 模型）体积较大，建议按部署场景选择是否内置。

## 运行排障（高频）

### 启动失败/健康检查失败

```bat
start_all.bat status
```

重点观察 8000/8001/8003/8002 是否全部可达。

### 提交任务时报 `E-UPSTREAM-START-FAILED`

常见原因：

- Django 数据库迁移未完成
- Django / Worker 未启动

修复命令：

```bat
.\.venv\Scripts\python.exe manage.py migrate
start_all.bat restart
```

### vLLM 不可达

- 检查 `LOCAL_VLLM_BASE_URL`
- 检查 8002 端口监听
- 检查模型路径与 API Key

## 版本与更新

- 版本号文件：`VERSION`
- 更新清单：`runtime/releases/update_manifest.json`
- 更新检查接口：`GET /contract/api/update/check/`

建议每次发版流程：

1. 更新 `VERSION`
2. 构建 Release
3. 生成安装包
4. 生成/更新清单与校验值
5. 回归测试后发布

## 数据与安全建议

- 生产环境不要提交 `.env`、模型权重、运行日志。
- 对外分发安装包时，建议附带 SHA256 校验值。
- 如需多机部署，优先使用内网分发并设置访问控制。

## 路线建议（后续）

- P1 完整收口：安装器稳定化、升级链路固化、日志诊断包标准化
- P2 能力演进：多合同批量审查、模板化规则策略、移动端适配
- P3 交付能力：组织级权限、审查资产沉淀、可观测性面板
