# Flutter 客户端

`apps/mobile_client_flutter` 是智能合同审查系统的统一前端，目标同时覆盖桌面端与未来移动端。

## 功能

- 上传 PDF
- 发起审查
- 轮询状态
- 查看可读化审查结果
- 导出 PDF 报告

## 默认 API 地址

- 桌面：`http://127.0.0.1:8003/contract`
- Android 模拟器：`http://10.0.2.2:8003/contract`

## 启动

### 一键（推荐）

```bat
.\launch_flutter_oneclick.bat
.\launch_flutter_release_oneclick.bat
```

### 手动指定设备

```bat
.\start_all.bat start
.\run_flutter_client.bat windows
.\run_flutter_client.bat android --debug
```

### 纯 Flutter 命令

```bat
cd apps\mobile_client_flutter
..\..\tools\flutter\bin\flutter.bat pub get
..\..\tools\flutter\bin\flutter.bat run -d windows
```

## 说明

Flutter 启动核心脚本已迁移到 `scripts/dev` 与 `scripts/release`，根目录命令保持兼容。
