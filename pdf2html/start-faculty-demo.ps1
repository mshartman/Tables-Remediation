$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & python faculty_demo.py
    if ($LASTEXITCODE -ne 0) { throw 'The faculty demo could not start. Check the Python dependencies and whether port 8002 is in use.' }
} finally {
    Pop-Location
}
