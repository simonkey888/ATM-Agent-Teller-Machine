$ErrorActionPreference = "Continue"

$ok = $true

Write-Host "ATM doctor" -ForegroundColor Cyan

if (Get-Command hermes -ErrorAction SilentlyContinue) {
    Write-Host "[OK] hermes: $((Get-Command hermes).Source)"
    hermes --version
} else {
    Write-Host "[FAIL] hermes missing" -ForegroundColor Red
    $ok = $false
}

if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Host "[OK] gh: $((Get-Command gh).Source)"
    gh auth status
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ACTION] run: gh auth login" -ForegroundColor Yellow
        $ok = $false
    }
} else {
    Write-Host "[FAIL] GitHub CLI missing" -ForegroundColor Red
    $ok = $false
}

$HermesEnv = Join-Path (Join-Path $env:LOCALAPPDATA "hermes") ".env"
if (Test-Path $HermesEnv) {
    $hasGoogle = Select-String -Path $HermesEnv -Pattern '^GOOGLE_API_KEY=.+' -Quiet
    if ($hasGoogle) {
        Write-Host "[OK] GOOGLE_API_KEY present in $HermesEnv (value not printed)"
    } else {
        Write-Host "[FAIL] GOOGLE_API_KEY missing in $HermesEnv" -ForegroundColor Red
        $ok = $false
    }
} else {
    Write-Host "[FAIL] Hermes env file not found at $HermesEnv" -ForegroundColor Red
    $ok = $false
}

if ($ok) {
    Write-Host "Core local prerequisites look ready for Gemini." -ForegroundColor Green
    exit 0
}
exit 1
