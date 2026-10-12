param(
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist")
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$packageName = "PDF-Table-Lab-Minimal-$timestamp"
$stagingPath = Join-Path $OutputDirectory $packageName
$zipPath = "$stagingPath.zip"

if (Test-Path -LiteralPath $stagingPath) {
    throw "Package staging path already exists: $stagingPath"
}

New-Item -ItemType Directory -Path $stagingPath -Force | Out-Null

$studentFolder = Join-Path $stagingPath "student_table_lab"
New-Item -ItemType Directory -Path $studentFolder -Force | Out-Null

$files = @(
    "table_lab_server.py",
    "requirements-table-lab.txt"
)

foreach ($file in $files) {
    $source = Join-Path $projectRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $stagingPath -Force
    }
}

$localStrategyPath = Join-Path $projectRoot "content_accessibility_utility_on_aws\remediate\remediation_strategies"
Copy-Item -LiteralPath (Join-Path $localStrategyPath "local_table_extractor.py") -Destination $stagingPath -Force
Copy-Item -LiteralPath (Join-Path $localStrategyPath "local_table_customization.py") -Destination $stagingPath -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "setup-table-lab.ps1") -Destination $studentFolder -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run-table-lab.ps1") -Destination $studentFolder -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $studentFolder -Force

Compress-Archive -Path (Join-Path $stagingPath "*") -DestinationPath $zipPath -Force
Write-Host "Created package: $zipPath"
Write-Host "The staging folder remains available for inspection: $stagingPath"
