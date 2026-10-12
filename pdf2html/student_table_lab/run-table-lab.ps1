$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Table Lab is not set up. Run .\student_table_lab\setup-table-lab.ps1 first."
}

Set-Location $projectRoot

Write-Host "Starting Table Lab at http://127.0.0.1:8000"
& $venvPython table_lab_server.py
