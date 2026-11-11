$previousKey = $env:LITELLM_API_KEY
$secret = Read-Host 'Campus LiteLLM API key (session only)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:LITELLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    Push-Location $PSScriptRoot
    try { python reader_local.py } finally { Pop-Location }
} finally {
    $env:LITELLM_API_KEY = $previousKey
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secret.Dispose()
}
