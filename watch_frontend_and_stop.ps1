$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $root 'scripts\ops\watch_frontend_and_stop.ps1'

if (!(Test-Path $target)) {
    throw "missing target script: $target"
}

& $target @args
exit $LASTEXITCODE
