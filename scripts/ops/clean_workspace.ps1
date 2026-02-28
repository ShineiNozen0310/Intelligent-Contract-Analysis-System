$ErrorActionPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root

Write-Output '[clean] remove known generated directories'
$targets = @(
    '.\apps\mobile_client_flutter\build',
    '.\apps\mobile_client_flutter\.dart_tool',
    '.\apps\mobile_client_flutter\ios\Flutter\ephemeral',
    '.\apps\mobile_client_flutter\linux\flutter\ephemeral',
    '.\apps\mobile_client_flutter\macos\Flutter\ephemeral',
    '.\apps\mobile_client_flutter\windows\flutter\ephemeral',
    '.\apps\mobile_client_flutter\.flutter-plugins-dependencies',
    '.\parsers\mineru\mineru.egg-info'
)

foreach ($p in $targets) {
    if (Test-Path $p) {
        Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
        Write-Output "[removed] $p"
    }
}

Write-Output '[clean] remove python cache files (exclude .venv/tools/flutter/.git/hf_models)'
$exclude = '\\.venv\\|\\tools\\flutter\\|\\.git\\|\\hf_models\\'

Get-ChildItem -Path . -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch $exclude -and $_.Name -eq '__pycache__' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Get-ChildItem -Path . -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch $exclude -and ($_.Extension -eq '.pyc' -or $_.Extension -eq '.pyo') } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Output '[ok] workspace cleanup finished'

