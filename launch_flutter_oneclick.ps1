$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

& "$root\start_all.bat" start
if ($LASTEXITCODE -ne 0) {
    throw "backend startup failed"
}

& "$root\run_flutter_client.bat"
if ($LASTEXITCODE -ne 0) {
    throw "flutter client startup failed"
}
