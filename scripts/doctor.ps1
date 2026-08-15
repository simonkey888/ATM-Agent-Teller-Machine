$ErrorActionPreference = "Continue"

$ok = $true

Write-Host "ATM doctor" -ForegroundColor Cyan

if (Get-Command hermes -ErrorAction SilentlyContinue) {
    Write-Host "[OK] hermes: $((Get-Command hermes).Source)"
    hermes --version
    hermes doctor
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

$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$HermesEnv = Join-Path $HermesHome ".env"
if (Test-Path $HermesEnv) {
    $hasGlm = Select-String -Path $HermesEnv -Pattern '^GLM_API_KEY=.+' -Quiet
    if ($hasGlm) {
        Write-Host "[OK] GLM_API_KEY present in $HermesEnv (value not printed)"
    } else {
        Write-Host "[INFO] No GLM_API_KEY in $HermesEnv. ATM can still use Qwen OAuth."
    }
} else {
    Write-Host "[INFO] Hermes env file not found at $HermesEnv. ATM can still use Qwen OAuth."
}

Write-Host ""
Write-Host "Qwen OAuth cannot be safely inferred from a secret dump."
Write-Host "If not already done: hermes model -> Qwen -> Qwen CLI OAuth -> qwen3-coder-plus"

if ($ok) {
    Write-Host "Core local prerequisites look ready." -ForegroundColor Green
    exit 0
}
exit 1
