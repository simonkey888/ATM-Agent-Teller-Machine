param([switch]$Once,[switch]$Status)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
if (-not (Test-Path ".\config\atm.json")) { Copy-Item ".\config\atm.example.json" ".\config\atm.json"; Write-Host "Created local config\atm.json" }
$argsList = @(".\src\atm_v2.py"); if ($Once) { $argsList += "--once" }; if ($Status) { $argsList += "--status" }
if (Get-Command uv -ErrorAction SilentlyContinue) { & uv run --python 3.11 python @argsList; exit $LASTEXITCODE }
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.11 @argsList; exit $LASTEXITCODE }
if (Get-Command python -ErrorAction SilentlyContinue) { & python @argsList; exit $LASTEXITCODE }
throw "Python/uv not found"
