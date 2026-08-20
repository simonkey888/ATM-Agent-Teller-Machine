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
        winget install --id GitHub.cli -e --source winget
    } else {
        throw "GitHub CLI missing and winget unavailable"
    }
}

Write-Host ""
Write-Host "ONE-TIME HUMAN ONBOARDING" -ForegroundColor Yellow
Write-Host "1) gh auth login"
Write-Host "2) hermes setup -> Google AI Studio / Gemini"
Write-Host "3) Store GOOGLE_API_KEY only in Hermes local env"
Write-Host "4) Optional WorkProtocol rail: register agent once; store WORKPROTOCOL_API_KEY + WORKPROTOCOL_AGENT_ID locally"
Write-Host "5) Set only your PUBLIC payout recipient in config\atm.json"
Write-Host ""
Write-Host "Never provide wallet private keys or seed phrases to ATM."
Write-Host "Then run: .\scripts\doctor.ps1"
