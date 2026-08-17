$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --python 3.11 python .\src\atm_control.py
    exit $LASTEXITCODE
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 .\src\atm_control.py
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python .\src\atm_control.py
    exit $LASTEXITCODE
}
throw "Python/uv not found"
