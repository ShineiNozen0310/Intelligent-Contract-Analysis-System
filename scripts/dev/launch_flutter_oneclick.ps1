param(
    [string]$Device = 'windows'
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root
if ($Device -in @('-h','--help','help')) {
    & "$root\\run_flutter_client.bat" --help
    exit $LASTEXITCODE
}


& "$root\start_all.bat" start
if ($LASTEXITCODE -ne 0) {
    throw "backend startup failed"
}

& "$root\run_flutter_client.bat" $Device
if ($LASTEXITCODE -ne 0) {
    throw "flutter client startup failed"
}
