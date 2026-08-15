$ErrorActionPreference = "Continue"

$ok = $true

Write-Host "ATM doctor" -ForegroundColor Cyan

if (Get-Command hermes -ErrorAction SilentlyContinue) {
    Write-Host "[OK] hermes: $((Get-Command hermes).Source)"
    hermes --version
    $doctorOut = (& hermes doctor 2>&1 | Out-String)
    Write-Host $doctorOut
    if ($doctorOut -match 'OpenAI Codex auth \(not logged in\)') {
        Write-Host "[ACTION] authenticate Codex in Hermes: hermes model -> OpenAI -> ChatGPT or Codex Subscription" -ForegroundColor Yellow
        $ok = $false
    } else {
        Write-Host "[OK] no unauthenticated OpenAI Codex warning detected"
    }
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
        Write-Host "[INFO] GLM_API_KEY present in $HermesEnv, but Z.AI fallback is disabled by default."
    }
}

if ($ok) {
    Write-Host "Core local prerequisites look ready." -ForegroundColor Green
    exit 0
}
exit 1
