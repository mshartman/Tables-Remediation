$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$secret = Read-Host 'OpenAI API key (hidden; used only for this session)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
$previous = $env:OPENAI_API_KEY
try {
    $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    python reader_local.py
} finally {
    $env:OPENAI_API_KEY = $previous
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secret.Dispose()
}
