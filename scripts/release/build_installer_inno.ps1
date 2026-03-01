param(
    [string]$Version = "",
    [switch]$SkipFlutterBuild,
    [switch]$IncludeVenv,
    [switch]$IncludeModels,
    [switch]$SkipStage,
    [string]$CustomStageDir = ""
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root

function Write-Info([string]$msg) { Write-Host "[P1-Inno] $msg" -ForegroundColor Cyan }

if ([string]::IsNullOrWhiteSpace($Version)) {
    if (Test-Path 'VERSION') { $Version = (Get-Content -Raw 'VERSION').Trim() }
    if ([string]::IsNullOrWhiteSpace($Version)) { $Version = '1.0.0' }
}

if (-not $SkipStage) {
    $stageCmd = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $root 'scripts\release\build_installer_nsis.ps1'),
        '-SkipNsisCompile'
    )
    if ($SkipFlutterBuild) { $stageCmd += '-SkipFlutterBuild' }
    if ($IncludeVenv) { $stageCmd += '-IncludeVenv' }
    if ($IncludeModels) { $stageCmd += '-IncludeModels' }
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $stageCmd += '-Version'
        $stageCmd += $Version
    }

    Write-Info 'Preparing stage payload...'
    & powershell.exe @stageCmd
    if ($LASTEXITCODE -ne 0) { throw "stage generation failed: $LASTEXITCODE" }
}

$stageDir = $CustomStageDir
if ([string]::IsNullOrWhiteSpace($stageDir)) {
    $latestFile = Join-Path $root 'runtime\releases\stage_latest.txt'
    if (!(Test-Path $latestFile)) { throw 'stage_latest.txt not found' }
    $stageDir = (Get-Content -Raw $latestFile).Trim()
}
if (!(Test-Path $stageDir)) {
    $fallbackStage = Join-Path $root 'runtime\releases\stage'
    if (Test-Path $fallbackStage) {
        Write-Info "stage_latest invalid, fallback to: $fallbackStage"
        $stageDir = $fallbackStage
    } else {
        throw "stage directory not found: $stageDir"
    }
}

$isccCmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
$iscc = if ($isccCmd) { $isccCmd.Source } else { '' }
if (!$iscc) {
    $cands = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $cands) {
        if (Test-Path $c) { $iscc = $c; break }
    }
}
if (!$iscc) {
    throw 'ISCC.exe not found. Please install Inno Setup 6 first.'
}

$iss = Join-Path $root 'scripts\release\inno\contract_review_full.iss'
if (!(Test-Path $iss)) { throw "missing inno script: $iss" }

$outDir = Join-Path $root 'runtime\releases'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$langFile = 'compiler:Default.isl'
$zhCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\Languages\ChineseSimplified.isl",
    "${env:ProgramFiles}\Inno Setup 6\Languages\ChineseSimplified.isl"
)
foreach ($f in $zhCandidates) {
    if ($f -and (Test-Path $f)) {
        $langFile = 'compiler:Languages\ChineseSimplified.isl'
        break
    }
}

Write-Info "Compiling Inno full installer (Version=$Version, Lang=$langFile)..."
& $iscc "/DAppVersion=$Version" "/DStageDir=$stageDir" "/DOutDir=$outDir" "/DLangFile=$langFile" $iss
if ($LASTEXITCODE -ne 0) { throw "Inno build failed: $LASTEXITCODE" }

Write-Info "Done. Output under: $outDir"
