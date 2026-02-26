# 合同审查系统（Desktop）

本项目用于 PDF 合同审查，包含 Django 后端、Worker 异步任务和桌面客户端。

## 1. 主要功能

- 上传 PDF，创建审查任务
- OCR 文本提取与盖章检测
- 调用 LLM 输出 `result_json`
- 导出 PDF 报告

## 2. 目录说明

```text
DjangoProject1/                Django 配置
contract_review/               Web 业务逻辑
contract_review_worker/        Worker 与 Celery 任务
desktop_app/                   桌面应用与 EXE 打包
start_all.bat                  启停一体脚本
```

## 3. 快速启动

```bat
start_all.bat start
```

其他常用命令：

```bat
start_all.bat stop
start_all.bat restart
start_all.bat status
```

## 4. 常用配置

- `REVIEW_MODE=auto|fast|accurate`
- `OCR_DPI`、`OCR_MAX_PAGES`
- `QWEN_TIMEOUT`、`QWEN_INPUT_MAX_CHARS`
- `STAMP_ENABLED`、`STAMP_MAX_PAGES`

## 5. 相关文档

- [桌面端说明](desktop_app/README.md)
- [Worker 说明](contract_review_worker/README.md)
