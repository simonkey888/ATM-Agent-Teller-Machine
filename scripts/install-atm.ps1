$ErrorActionPreference = "Stop"

Write-Host "ATM bootstrap: Hermes + GitHub CLI" -ForegroundColor Cyan

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Hermes Agent from official installer..."
    iex (irm https://hermes-agent.nousresearch.com/install.ps1)
} else {
    Write-Host "Hermes already installed."
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing GitHub CLI with winget..."
        winget install --id GitHub.cli -e --source winget
    } else {
        Write-Host "GitHub CLI is required. Download: https://github.com/cli/cli/releases/latest" -ForegroundColor Yellow
    }
} else {
    Write-Host "GitHub CLI already installed."
}

Write-Host ""
Write-Host "ONE-TIME HUMAN ONBOARDING" -ForegroundColor Yellow
Write-Host "1) gh auth login"
Write-Host "2) hermes model  -> Qwen OAuth (Portal) -> qwen3-coder-plus"
Write-Host "3) Optional GLM free fallback key: https://z.ai/manage-apikey/apikey-list"
Write-Host "4) Add GLM_API_KEY to $HOME\.hermes\.env"
Write-Host ""
Write-Host "Then run: .\scripts\doctor.ps1"
