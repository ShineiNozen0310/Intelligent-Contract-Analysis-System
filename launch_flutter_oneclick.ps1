$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $root 'scripts\dev\launch_flutter_oneclick.ps1'

if (!(Test-Path $target)) {
    throw "missing target script: $target"
}

& $target @args
exit $LASTEXITCODE
