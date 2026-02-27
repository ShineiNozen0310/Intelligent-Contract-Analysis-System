$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')

$launcherPs1 = Join-Path $root 'launch_flutter_release_oneclick.ps1'
if (!(Test-Path $launcherPs1)) {
    throw "missing launcher: $launcherPs1"
}

$psExe = (Get-Command powershell.exe).Source
$args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherPs1`""

$icon = Join-Path $root '合同审查图标.ico'
if (!(Test-Path $icon)) {
    $icon = $launcherPs1
}

$links = @(
    (Join-Path $root '合同审查桌面版.lnk'),
    (Join-Path $desktop '合同审查桌面版.lnk')
)

$ws = New-Object -ComObject WScript.Shell
foreach ($lnk in $links) {
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $psExe
    $s.Arguments = $args
    $s.WorkingDirectory = $root
    $s.WindowStyle = 7
    $s.IconLocation = "$icon,0"
    $s.Description = '合同智能审查（Flutter 桌面版）'
    $s.Save()
    Write-Output "[ok] $lnk"
}
