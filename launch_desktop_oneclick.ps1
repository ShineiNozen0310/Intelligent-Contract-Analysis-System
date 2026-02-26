$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Start-DesktopClient {
    $exe = Join-Path $root "desktop_app\dist\ContractReviewDesktop.exe"
    if (Test-Path $exe) {
        return Start-Process -FilePath $exe -PassThru
    }
    $runner = Join-Path $root "desktop_app\run_app.bat"
    return Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$runner`"" -PassThru
}

$appProc = Start-DesktopClient
if ($null -ne $appProc) {
    Wait-Process -Id $appProc.Id
}
