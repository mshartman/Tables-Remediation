param(
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist")
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$packageName = "PDF-Table-Lab-$timestamp"
$stagingPath = Join-Path $OutputDirectory $packageName
$zipPath = "$stagingPath.zip"

if (Test-Path -LiteralPath $stagingPath) {
    throw "Package staging path already exists: $stagingPath"
}

New-Item -ItemType Directory -Path $stagingPath -Force | Out-Null

$folders = @(
    "content_accessibility_utility_on_aws",
    "templates",
    "student_table_lab"
)
$files = @(
    "local_server.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "README_LOCAL_SERVER.md",
    "README_TABLE_REMEDIATION.md",
    "LICENSE",
    "THIRD-PARTY-LICENSES"
)

foreach ($folder in $folders) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $folder) -Destination $stagingPath -Recurse -Force
}

foreach ($file in $files) {
    $source = Join-Path $projectRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $stagingPath -Force
    }
}

Compress-Archive -Path (Join-Path $stagingPath "*") -DestinationPath $zipPath -Force
Write-Host "Created package: $zipPath"
Write-Host "The staging folder remains available for inspection: $stagingPath"
