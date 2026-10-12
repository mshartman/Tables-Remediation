$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue

if (-not $pythonLauncher) {
    throw "Python 3.12 or later is required. Install Python from python.org, then run this script again."
}

& py -3 --version
if ($LASTEXITCODE -ne 0) {
    throw "The Python launcher could not start Python 3. Install Python 3.12 or later, then retry."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating a local Python environment..."
    & py -3 -m venv (Join-Path $projectRoot ".venv")
}

Write-Host "Installing Table Lab dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements-table-lab.txt")

Write-Host "Setup complete. Run .\student_table_lab\run-table-lab.ps1 to start the local Table Lab."
