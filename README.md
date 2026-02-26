# 合同智能审查系统（Desktop）

> 面向本地部署的合同审查桌面应用。对外是一个可分发的桌面端，对内托管 Django API、Worker、Celery 等服务。

---

## 目录

- [产品概述](#产品概述)
- [核心能力](#核心能力)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [常用命令](#常用命令)
- [关键配置](#关键配置)
- [项目结构](#项目结构)
- [文档导航（分层）](#文档导航分层)
- [常见问题](#常见问题)

---

## 产品概述

本项目的定位是“真正可用的桌面软件”，不是网页站点：

- 用户只操作桌面前端窗口
- 后端服务在本机后台启动与管理
- 关闭桌面应用时自动回收服务
- 支持 PDF 合同审查、结构化结果展示、报告导出

---

## 核心能力

### 审查能力

- PDF 上传与任务管理
- OCR 文本提取（PaddleOCR）
- 盖章检测（Stamp）
- LLM 生成结构化审查结果（`result_json`）
- 报告展示、复制与 PDF 导出

### 工程能力

- 一键启动/停止本地全套服务
- 桌面端自动健康检查与重试
- 模型与缓存本地化（`hf_models/`）
- 可打包为 `exe` 分发

---

## 系统架构

```text
Desktop App (PySide6)
    -> Django API (127.0.0.1:8000)
    -> Worker API (127.0.0.1:8001)
    -> Celery + Redis (后台异步任务)
    -> Local Models / OCR / LLM
```

说明：
- 8000/8001 是本机回环端口，仅本机进程通信，不对公网开放。
- 虽然保留 Django/Worker 服务层，但产品入口始终是桌面端。

---

## 快速开始

### 1. 运行环境

- Windows 10/11
- Python 3.10（建议使用项目内 `.venv`）
- Redis（本地）

### 2. 安装依赖

```bat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

GPU 版本可选：

```bat
pip install -r requirements-gpu.txt
```

### 3. 启动方式

推荐（桌面一键启动）：

```bat
launch_desktop_oneclick.bat
```

开发调试（服务脚本）：

```bat
start_all.bat start
```

---

## 常用命令

```bat
start_all.bat start
start_all.bat stop
start_all.bat restart
start_all.bat status
```

打包桌面端：

```bat
desktop_app\build_exe.bat
```

输出：

```text
desktop_app/dist/ContractReviewDesktop.exe
```

---

## 关键配置

配置文件：根目录 `.env`

高频参数：

- `REVIEW_MODE=fast|accurate|auto`
- `OCR_DPI`、`OCR_MAX_PAGES`
- `PADDLEOCR_HOME`
- `QWEN_TIMEOUT`、`QWEN_INPUT_MAX_CHARS`
- `STAMP_ENABLED`、`STAMP_MAX_PAGES`
- `CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`

---

## 项目结构

```text
DjangoProject1/                Django 配置
contract_review/               Django 业务接口层
contract_review_worker/        审查 Worker（OCR/盖章/LLM）
desktop_app/                   桌面应用与 EXE 打包
hf_models/                     本地模型与缓存
parsers/                       第三方解析能力（subtree）
start_all.bat                  服务启停脚本
launch_desktop_oneclick.bat    桌面一键启动入口
```

---

## 文档导航（分层）

### L1：产品入口文档（对用户）

- [桌面端说明](desktop_app/README.md)

### L2：后端子模块文档（对开发/运维）

- [Worker 服务说明](contract_review_worker/README.md)

说明：Worker 属于后端实现层，由桌面端托管启动，不与桌面端处于同一产品层级。

---

## 常见问题

### Q1：为什么有 8000 和 8001 两个端口？

用于本机模块通信：Django API 与 Worker API 分离，便于隔离与维护。

### Q2：复制项目到另一台电脑能直接运行吗？

不一定。需要确认：

- Python/依赖安装完成
- `.env` 路径与新机器一致
- Redis 可用
- 本地模型缓存已同步

### Q3：启动后只想看到前端窗口，不要后端黑窗？

使用 `launch_desktop_oneclick.bat` 或打包后的 `exe`，不要直接手动开多个后端控制台。
