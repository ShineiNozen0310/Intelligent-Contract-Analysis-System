param(
    [string]$Version = "",
    [switch]$SkipFlutterBuild,
    [switch]$SkipNsisCompile,
    [switch]$IncludeVenv,
    [switch]$IncludeModels
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root

function Write-Info([string]$msg) {
    Write-Host "[P1] $msg" -ForegroundColor Cyan
}

function Resolve-Version {
    param([string]$Preferred)
    if (![string]::IsNullOrWhiteSpace($Preferred)) { return $Preferred.Trim() }

    $vfile = Join-Path $root 'VERSION'
    if (Test-Path $vfile) {
        $v = (Get-Content -Raw $vfile).Trim()
        if ($v) { return $v }
    }

    if ($env:APP_CURRENT_VERSION) {
        $v = $env:APP_CURRENT_VERSION.Trim()
        if ($v) { return $v }
    }

    return '1.0.0'
}

function Ensure-ReleaseExe {
    if ($SkipFlutterBuild) { return }
    $buildBat = Join-Path $root 'build_flutter_release.bat'
    if (!(Test-Path $buildBat)) {
        throw "missing build script: $buildBat"
    }
    Write-Info 'Building Flutter release executable...'
    cmd.exe /c "`"$buildBat`""
    if ($LASTEXITCODE -ne 0) {
        throw "flutter release build failed with code $LASTEXITCODE"
    }
}

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target,
        [string[]]$ExtraExcludeDirs = @()
    )

    if (!(Test-Path $Source)) { return }
    New-Item -ItemType Directory -Force -Path $Target | Out-Null

    $args = @(
        $Source,
        $Target,
        '/E',
        '/R:1',
        '/W:1',
        '/NFL',
        '/NDL',
        '/NJH',
        '/NJS',
        '/NP',
        '/XD', (Join-Path $Source '.git'),
        '/XD', (Join-Path $Source '.idea'),
        '/XD', (Join-Path $Source '.vscode'),
        '/XD', (Join-Path $Source '__pycache__'),
        '/XD', (Join-Path $Source '.pytest_cache'),
        '/XF', '*.pyc',
        '/XF', '*.pyo',
        '/XF', '*.log',
        '/XF', 'Thumbs.db',
        '/XF', '.DS_Store'
    )

    foreach ($x in $ExtraExcludeDirs) {
        $args += '/XD'
        $args += (Join-Path $Source $x)
    }

    robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed for $Source (exit=$LASTEXITCODE)"
    }
}

$version = Resolve-Version -Preferred $Version
[System.IO.File]::WriteAllText((Join-Path $root 'VERSION'), "$version`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Info "Using version: $version"

Ensure-ReleaseExe

$releaseExe = Join-Path $root 'apps\mobile_client_flutter\build\windows\x64\runner\Release\contract_review_flutter.exe'
if (!(Test-Path $releaseExe)) {
    throw "release exe not found: $releaseExe"
}

$outRoot = Join-Path $root 'runtime\releases'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stageRoot = Join-Path $outRoot ("stage_" + $timestamp)
New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
[System.IO.File]::WriteAllText((Join-Path $outRoot 'stage_latest.txt'), "$stageRoot`n", (New-Object System.Text.UTF8Encoding($false)))

Write-Info 'Staging project files for installer...'

$rootFiles = @(
    'README.md',
    'VERSION',
    '.env.example',
    'requirements.txt',
    'requirements-gpu.txt',
    'llm_gateway.yaml',
    'manage.py',
    'start_all.bat',
    'stop_all.bat',
    'start_llm_gateway.bat',
    'stop_llm_gateway.bat',
    'launch_flutter_release_oneclick.bat',
    'launch_flutter_release_oneclick.ps1',
    'watch_frontend_and_stop.ps1',
    'contract_review_launcher.ico'
)

foreach ($f in $rootFiles) {
    $src = Join-Path $root $f
    if (Test-Path $src) {
        $dst = Join-Path $stageRoot $f
        $parent = Split-Path -Parent $dst
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -Force $src $dst
    }
}

$exclude = @('runtime', 'tools')
if (-not $IncludeVenv) { $exclude += '.venv' }
if (-not $IncludeModels) {
    $exclude += 'hf_models'
    $exclude += 'models'
}

$dirs = @('apps', 'contract_review', 'contract_review_worker', 'DjangoProject1', 'packages', 'scripts')
foreach ($d in $dirs) {
    $srcDir = Join-Path $root $d
    $dstDir = Join-Path $stageRoot $d

    $dirExclude = @($exclude)
    if ($d -eq 'apps') {
        $dirExclude += @(
            'mobile_client_flutter\build',
            'mobile_client_flutter\.dart_tool',
            'mobile_client_flutter\.idea',
            'mobile_client_flutter\.plugin_symlinks',
            'mobile_client_flutter\linux\flutter\ephemeral',
            'mobile_client_flutter\windows\flutter\ephemeral',
            'mobile_client_flutter\macos\Flutter\ephemeral',
            'mobile_client_flutter\ios\Flutter\ephemeral'
        )
    }

    Copy-Tree -Source $srcDir -Target $dstDir -ExtraExcludeDirs $dirExclude
}

# Keep Flutter desktop runtime payload even when excluding build cache.
$releaseRuntimeDir = Join-Path $root 'apps\mobile_client_flutter\build\windows\x64\runner\Release'
$releaseRuntimeDst = Join-Path $stageRoot 'apps\mobile_client_flutter\build\windows\x64\runner\Release'
Copy-Tree -Source $releaseRuntimeDir -Target $releaseRuntimeDst

# Optional full-package payloads.
if ($IncludeVenv) {
    $venvSrc = Join-Path $root '.venv'
    $venvDst = Join-Path $stageRoot '.venv'
    Copy-Tree -Source $venvSrc -Target $venvDst
}
if ($IncludeModels) {
    $hfSrc = Join-Path $root 'hf_models'
    $hfDst = Join-Path $stageRoot 'hf_models'
    Copy-Tree -Source $hfSrc -Target $hfDst

    $modelsSrc = Join-Path $root 'models'
    $modelsDst = Join-Path $stageRoot 'models'
    Copy-Tree -Source $modelsSrc -Target $modelsDst
}

if (-not $IncludeVenv) {
    Write-Info 'Skipping .venv (pass -IncludeVenv to include offline python runtime).'
}
if (-not $IncludeModels) {
    Write-Info 'Skipping hf_models/models (pass -IncludeModels to include local model weights).'
}

if ($SkipNsisCompile) {
    Write-Info "Stage ready: $stageRoot"
    exit 0
}

$nsisScript = Join-Path $root 'scripts\release\nsis\contract_review_installer.nsi'
if (!(Test-Path $nsisScript)) {
    throw "missing NSIS script: $nsisScript"
}

$makensisCmd = Get-Command 'makensis.exe' -ErrorAction SilentlyContinue
$makensis = if ($makensisCmd) { $makensisCmd.Source } else { '' }
if (!$makensis) {
    $candidates = @(
        "$env:ProgramFiles\NSIS\makensis.exe",
        "${env:ProgramFiles(x86)}\NSIS\makensis.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $makensis = $c
            break
        }
    }
}
if (!$makensis) {
    throw 'makensis.exe not found. Install NSIS first, or run with -SkipNsisCompile.'
}

New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
Write-Info 'Compiling NSIS installer...'

& $makensis "/DAPP_VERSION=$version" "/DAPP_STAGE=$stageRoot" $nsisScript
if ($LASTEXITCODE -ne 0) {
    throw "NSIS build failed with code $LASTEXITCODE"
}

Write-Info "Installer ready under: $outRoot"
