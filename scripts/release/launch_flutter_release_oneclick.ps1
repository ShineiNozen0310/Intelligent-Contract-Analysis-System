$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root

$logDir = Join-Path $root 'runtime\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir 'launcher_oneclick.log'

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

try {
    $exe = Join-Path $root 'apps\mobile_client_flutter\build\windows\x64\runner\Release\contract_review_flutter.exe'
    if (!(Test-Path $exe)) {
        Write-Log 'Release exe not found, building flutter release...'
        & (Join-Path $root 'build_flutter_release.bat')
        if ($LASTEXITCODE -ne 0) {
            throw 'Flutter release build failed.'
        }
    }

    $frontend = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent) -PassThru
    Write-Log ("Frontend started. pid={0}" -f $frontend.Id)

    $watchScript = Join-Path $root 'watch_frontend_and_stop.ps1'
    if (Test-Path $watchScript) {
        Start-Process -FilePath powershell.exe -ArgumentList @(
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-WindowStyle',
            'Hidden',
            '-File',
            $watchScript,
            '-FrontendPid',
            "$($frontend.Id)",
            '-RootPath',
            $root
        ) -WindowStyle Hidden | Out-Null
        Write-Log 'Frontend watcher started.'
    }

    $warmupScript = Join-Path $root 'scripts\release\warmup_backend_hidden.ps1'
    if (Test-Path $warmupScript) {
        Start-Process -FilePath powershell.exe -ArgumentList @(
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-WindowStyle',
            'Hidden',
            '-File',
            $warmupScript,
            '-RootPath',
            $root
        ) -WindowStyle Hidden | Out-Null
        Write-Log 'Backend warmup started in background.'
    } else {
        Write-Log "Warmup script not found: $warmupScript"
    }

    exit 0
}
catch {
    $msg = "前端启动失败：$($_.Exception.Message)`n请查看日志：$logFile"
    Write-Log $msg
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($msg, '合同审查桌面版启动失败') | Out-Null
    } catch {}
    exit 1
}
