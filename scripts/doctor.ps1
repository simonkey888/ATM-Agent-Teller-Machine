$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "ATM doctor — deterministic/read-only by default" -ForegroundColor Cyan

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { throw "Hermes missing" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "GitHub CLI missing" }

hermes --version
gh --version

if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --python 3.11 python .\src\doctor.py @args
    exit $LASTEXITCODE
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 .\src\doctor.py @args
    exit $LASTEXITCODE
}
python .\src\doctor.py @args
exit $LASTEXITCODE
