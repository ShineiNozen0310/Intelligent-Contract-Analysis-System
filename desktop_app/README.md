# 桌面应用说明

`desktop_app` 是合同审查系统的桌面端入口，目标是“像普通软件一样使用”：
- 启动后只显示前端窗口
- 后端服务（Django + Worker + Celery）在后台无窗口运行
- 关闭前端时自动停止后台服务

## 1. 成熟化能力

- 单实例运行：重复打开会提示“应用已在运行”
- 后端自动拉起：首次启动自动检测并启动本地服务
- 健康守护：运行中服务掉线会自动尝试恢复
- 失败可视化：启动失败会在前端显示明确原因
- 日志落盘：日志自动滚动，便于排障和用户反馈

## 2. 运行方式

优先使用一键启动：

```bat
launch_desktop_oneclick.bat
```

或直接运行：

```bat
python desktop_app/app_pyside6.py
```

## 3. 打包为 EXE

```bat
desktop_app/build_exe.bat
```

输出文件：

```text
desktop_app/dist/ContractReviewDesktop.exe
```

## 4. 日志位置

Windows 日志目录：

```text
%LOCALAPPDATA%\ContractReviewDesktop\logs\desktop.log
```

应用内“日志”页也会显示实时运行日志。

## 5. 常见故障

- 提示 `Redis not reachable`：
  先启动 Redis，再重试“重启服务”。

- 提示 `Python runtime not found`：
  确认项目根目录有 `.venv`，或设置环境变量 `CONTRACT_REVIEW_PYTHON` 指向 Python 可执行文件。

- 启动后状态长期离线：
  查看日志文件末尾，重点检查 `django/worker/celery` 的错误信息。
