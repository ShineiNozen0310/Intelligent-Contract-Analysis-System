$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root

$started = New-Object System.Collections.Generic.List[object]
$launchSucceeded = $false

function Test-TcpPort {
    param(
        [string]$TargetHost = '127.0.0.1',
        [int]$Port,
        [int]$TimeoutMs = 900
    )
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($TargetHost, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            $c.EndConnect($iar) | Out-Null
            $c.Close()
            return $true
        }
        $c.Close()
        return $false
    } catch {
        return $false
    }
}

function Test-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 2
    )
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$MaxSeconds = 30
    )
    $limit = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $limit) {
        if (Test-HttpOk -Url $Url -TimeoutSec 2) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Start-HiddenTracked {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [string]$WorkingDirectory = $root
    )
    $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru
    $started.Add([pscustomobject]@{ Name = $Name; Pid = $p.Id }) | Out-Null
    return $p
}

function Stop-TrackedProcesses {
    param([System.Collections.Generic.List[object]]$Items)
    for ($i = $Items.Count - 1; $i -ge 0; $i--) {
        $item = $Items[$i]
        if (-not $item) { continue }
        $pid = [int]$item.Pid
        if ($pid -le 0) { continue }
        try { cmd /c "taskkill /PID $pid /T /F >nul 2>nul" | Out-Null } catch {}
    }
}

function Read-DotEnv {
    param([string]$Path)
    $map = @{}
    if (!(Test-Path $Path)) { return $map }
    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.TrimStart().StartsWith('#')) { continue }
        if ($line -notmatch '=') { continue }
        $parts = $line -split '=', 2
        $k = $parts[0].Trim()
        if ($k -eq '') { continue }
        $v = $parts[1].Trim()
        if ($v.Length -ge 2 -and $v.StartsWith('"') -and $v.EndsWith('"')) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

$py = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path $py)) { throw '未找到 .venv\\Scripts\\python.exe，请先创建虚拟环境并安装依赖。' }

$envMap = Read-DotEnv -Path (Join-Path $root '.env')

try {
    $vllmEnabled = $false
    $provider = ($envMap['LLM_PROVIDER'] | ForEach-Object { $_ })
    $primaryProvider = ($envMap['LLM_PRIMARY_PROVIDER'] | ForEach-Object { $_ })
    $requireLocalVllm = (($envMap['LLM_REQUIRE_LOCAL_VLLM'] | ForEach-Object { $_ }) -eq '1')
    if ((($envMap['VLLM_ENABLED'] | ForEach-Object { $_ }) -eq '1') -or $requireLocalVllm) { $vllmEnabled = $true }
    if ($provider -match '^(local_vllm|local|vllm)$') { $vllmEnabled = $true }
    if ($primaryProvider -match '^(local_vllm|local|vllm)$') { $vllmEnabled = $true }

    $localVllmPort = 8002
    if ($envMap.ContainsKey('LOCAL_VLLM_PORT')) {
        $tmp = 0
        if ([int]::TryParse($envMap['LOCAL_VLLM_PORT'], [ref]$tmp)) { $localVllmPort = $tmp }
    }

    if ($vllmEnabled -and !(Test-TcpPort -Port $localVllmPort)) {
        $vllmCmd = $envMap['LOCAL_VLLM_START_CMD']
        if (![string]::IsNullOrWhiteSpace($vllmCmd)) {
            Start-HiddenTracked -Name 'vllm-cmd' -FilePath 'cmd.exe' -ArgumentList @('/c', $vllmCmd) | Out-Null
        } elseif ($requireLocalVllm) {
            $vpy = $py
            if ($envMap.ContainsKey('LOCAL_VLLM_PYTHON') -and ![string]::IsNullOrWhiteSpace($envMap['LOCAL_VLLM_PYTHON'])) { $vpy = $envMap['LOCAL_VLLM_PYTHON'] }
            $host = if ($envMap['LOCAL_VLLM_HOST']) { $envMap['LOCAL_VLLM_HOST'] } else { '127.0.0.1' }
            $model = if ($envMap['LOCAL_VLLM_MODEL']) { $envMap['LOCAL_VLLM_MODEL'] } else { '.\hf_models\Qwen3-8B-AWQ' }
            $served = if ($envMap['LOCAL_VLLM_SERVED_MODEL']) { $envMap['LOCAL_VLLM_SERVED_MODEL'] } else { $model }
            $apiKey = if ($envMap['LOCAL_VLLM_API_KEY']) { $envMap['LOCAL_VLLM_API_KEY'] } else { 'dummy' }
            $extra = if ($envMap['LOCAL_VLLM_EXTRA_ARGS']) { $envMap['LOCAL_VLLM_EXTRA_ARGS'] } else { '' }
            $cmd = "`"$vpy`" -m vllm serve `"$model`" --host $host --port $localVllmPort --served-model-name `"$served`" --api-key `"$apiKey`" $extra"
            Start-HiddenTracked -Name 'vllm-python' -FilePath 'cmd.exe' -ArgumentList @('/c', $cmd) | Out-Null
        }
    }

    if (!(Test-HttpOk -Url 'http://127.0.0.1:8001/healthz')) {
        Start-HiddenTracked -Name 'worker' -FilePath $py -ArgumentList @('-m', 'uvicorn', 'contract_review_worker.api.main:app', '--host', '127.0.0.1', '--port', '8001') | Out-Null
    }

    $celeryRunning = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*celery*contract_review_worker.celery_app*worker*' } | Select-Object -First 1
    if (-not $celeryRunning) {
        Start-HiddenTracked -Name 'celery' -FilePath $py -ArgumentList @('-m', 'celery', '-A', 'contract_review_worker.celery_app', 'worker', '-l', 'info', '-P', 'solo') | Out-Null
    }

    if (!(Test-HttpOk -Url 'http://127.0.0.1:8000/contract/api/health/')) {
        Start-HiddenTracked -Name 'django' -FilePath $py -ArgumentList @('manage.py', 'runserver', '127.0.0.1:8000', '--noreload') | Out-Null
    }

    if (!(Test-HttpOk -Url 'http://127.0.0.1:8003/contract/api/health/')) {
        Start-HiddenTracked -Name 'local_api' -FilePath $py -ArgumentList @('-m', 'uvicorn', 'apps.local_api.main:app', '--host', '127.0.0.1', '--port', '8003') | Out-Null
    }

    if (!(Wait-HttpOk -Url 'http://127.0.0.1:8003/contract/api/health/' -MaxSeconds 40)) {
        throw '本地 API 启动超时（8003）。'
    }

    $exe = Join-Path $root 'apps\mobile_client_flutter\build\windows\x64\runner\Release\contract_review_flutter.exe'
    if (!(Test-Path $exe)) {
        & (Join-Path $root 'build_flutter_release.bat')
        if ($LASTEXITCODE -ne 0) { throw 'Flutter release 构建失败。' }
    }

    $frontend = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent) -PassThru

    $watchScript = Join-Path $root 'watch_frontend_and_stop.ps1'
    if (Test-Path $watchScript) {
        Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$watchScript,'-FrontendPid',"$($frontend.Id)",'-RootPath',$root) -WindowStyle Hidden | Out-Null
    }

    $launchSucceeded = $true
}
finally {
    if (-not $launchSucceeded) {
        if ($started.Count -gt 0) {
            Stop-TrackedProcesses -Items $started
        }
        $stopBat = Join-Path $root 'stop_all.bat'
        if (Test-Path $stopBat) {
            try { & cmd.exe /c "`"$stopBat`"" | Out-Null } catch {}
        }
    }
}

