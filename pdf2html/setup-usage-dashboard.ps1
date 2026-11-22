param([switch]$Replace)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $arguments = @('usage_dashboard.py', 'install')
    if ($Replace) { $arguments += '--replace' }
    & python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard setup failed. See the message above; do not rebuild the AWS image.' }
} finally {
    Pop-Location
}
