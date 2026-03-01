param(
    [string]$Version = "",
    [string]$InstallerPath = "",
    [string]$DownloadUrl = "",
    [string]$Notes = "",
    [string]$OutFile = ""
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = if (Test-Path 'VERSION') { (Get-Content -Raw 'VERSION').Trim() } else { '1.0.0' }
}
if ([string]::IsNullOrWhiteSpace($OutFile)) {
    $OutFile = (Join-Path $root 'runtime\releases\update_manifest.json')
}

if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $candidate = Join-Path $root ("runtime\releases\SmartContractReview_Setup_v$Version.exe")
    if (Test-Path $candidate) {
        $InstallerPath = $candidate
    }
}

$sha256 = ''
if (-not [string]::IsNullOrWhiteSpace($InstallerPath) -and (Test-Path $InstallerPath)) {
    $sha256 = (Get-FileHash $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

if ([string]::IsNullOrWhiteSpace($DownloadUrl) -and -not [string]::IsNullOrWhiteSpace($InstallerPath)) {
    $DownloadUrl = (Split-Path -Leaf $InstallerPath)
}

$payload = [ordered]@{
    version = $Version
    download_url = $DownloadUrl
    sha256 = $sha256
    notes = $Notes
    published_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
}

$parent = Split-Path -Parent $OutFile
if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}

$payload | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $OutFile
Write-Host "[P1] update manifest generated: $OutFile"
