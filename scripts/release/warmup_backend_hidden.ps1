param(
    [string]$RootPath = ''
)

$ErrorActionPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($RootPath)) {
    $RootPath = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
}
$root = $RootPath
Set-Location $root

$logDir = Join-Path $root 'runtime\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir 'launcher_backend_bootstrap.log'

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

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

function Start-Hidden {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [string]$WorkingDirectory = $root
    )
    Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -WindowStyle Hidden | Out-Null
    Write-Log ("start {0}" -f $Name)
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

try {
    Write-Log 'backend warmup begin'

    $py = Join-Path $root '.venv\Scripts\python.exe'
    if (!(Test-Path $py)) {
        Write-Log "skip warmup: missing python $py"
        exit 0
    }

    $envMap = Read-DotEnv -Path (Join-Path $root '.env')

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
            Start-Hidden -Name 'vllm-cmd' -FilePath 'cmd.exe' -ArgumentList @('/c', $vllmCmd)
        } elseif ($requireLocalVllm) {
            $vpy = $py
            if ($envMap.ContainsKey('LOCAL_VLLM_PYTHON') -and ![string]::IsNullOrWhiteSpace($envMap['LOCAL_VLLM_PYTHON'])) { $vpy = $envMap['LOCAL_VLLM_PYTHON'] }
            $host = if ($envMap['LOCAL_VLLM_HOST']) { $envMap['LOCAL_VLLM_HOST'] } else { '127.0.0.1' }
            $model = if ($envMap['LOCAL_VLLM_MODEL']) { $envMap['LOCAL_VLLM_MODEL'] } else { '.\hf_models\Qwen3-8B-AWQ' }
            $served = if ($envMap['LOCAL_VLLM_SERVED_MODEL']) { $envMap['LOCAL_VLLM_SERVED_MODEL'] } else { $model }
            $apiKey = if ($envMap['LOCAL_VLLM_API_KEY']) { $envMap['LOCAL_VLLM_API_KEY'] } else { 'dummy' }
            $extra = if ($envMap['LOCAL_VLLM_EXTRA_ARGS']) { $envMap['LOCAL_VLLM_EXTRA_ARGS'] } else { '' }
            $cmd = "`"$vpy`" -m vllm serve `"$model`" --host $host --port $localVllmPort --served-model-name `"$served`" --api-key `"$apiKey`" $extra"
            Start-Hidden -Name 'vllm-python' -FilePath 'cmd.exe' -ArgumentList @('/c', $cmd)
        } else {
            Write-Log 'vllm enabled but not required and no startup cmd configured, skip start.'
        }
    }

    if (!(Test-HttpOk -Url 'http://127.0.0.1:8001/healthz')) {
        Start-Hidden -Name 'worker' -FilePath $py -ArgumentList @('-m', 'uvicorn', 'contract_review_worker.api.main:app', '--host', '127.0.0.1', '--port', '8001')
    }

    $celeryRunning = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*celery*contract_review_worker.celery_app*worker*' } | Select-Object -First 1
    if (-not $celeryRunning) {
        Start-Hidden -Name 'celery' -FilePath $py -ArgumentList @('-m', 'celery', '-A', 'contract_review_worker.celery_app', 'worker', '-l', 'info', '-P', 'solo')
    }

    if (!(Test-HttpOk -Url 'http://127.0.0.1:8000/contract/api/health/')) {
        $djangoHost = if ($envMap['DJANGO_HOST']) { $envMap['DJANGO_HOST'] } else { '127.0.0.1' }
        $djangoPort = if ($envMap['DJANGO_PORT']) { $envMap['DJANGO_PORT'] } else { '8000' }
        $djangoMode = if ($envMap['DJANGO_SERVER_MODE']) { $envMap['DJANGO_SERVER_MODE'].ToLowerInvariant() } else { 'waitress' }

        if ($djangoMode -eq 'runserver') {
            Start-Hidden -Name 'django' -FilePath $py -ArgumentList @('manage.py', 'runserver', "$djangoHost`:$djangoPort", '--noreload')
        } else {
            Start-Hidden -Name 'django' -FilePath $py -ArgumentList @('-m', 'waitress', '--listen', "$djangoHost`:$djangoPort", 'DjangoProject1.wsgi:application')
        }
    }

    if (!(Test-HttpOk -Url 'http://127.0.0.1:8003/contract/api/health/')) {
        Start-Hidden -Name 'local_api' -FilePath $py -ArgumentList @('-m', 'uvicorn', 'apps.local_api.main:app', '--host', '127.0.0.1', '--port', '8003')
    }

    Write-Log 'backend warmup done'
}
catch {
    Write-Log ("backend warmup error: {0}" -f $_.Exception.Message)
}
