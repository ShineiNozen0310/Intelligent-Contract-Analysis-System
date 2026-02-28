param(
    [Parameter(Mandatory = $true)][int]$FrontendPid,
    [Parameter(Mandatory = $true)][string]$RootPath
)

$ErrorActionPreference = 'SilentlyContinue'

while ($true) {
    Start-Sleep -Milliseconds 900
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$FrontendPid"
    if (-not $proc) { break }
    if ($proc.Name -ne 'contract_review_flutter.exe') { break }
}


$otherFrontends = @(Get-CimInstance Win32_Process -Filter "Name='contract_review_flutter.exe'")
if ($otherFrontends.Count -gt 0) {
    exit 0
}

$stopBat = Join-Path $RootPath 'stop_all.bat'
if (Test-Path $stopBat) {
    cmd.exe /c "`"$stopBat`"" | Out-Null
}
