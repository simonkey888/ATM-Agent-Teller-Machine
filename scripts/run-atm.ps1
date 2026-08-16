param([switch]$Once,[switch]$Status)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $Status) {
    $branch = "agent/atm-v1"
    $currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $currentBranch -ne $branch) { throw "ATM must start from branch $branch; current=$currentBranch" }
    $dirty = git status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0) { throw "git status failed" }
    if ($dirty) { throw "Tracked local changes present; refusing automatic START sync" }
    git fetch origin $branch
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
    $remote = (git rev-parse "origin/$branch").Trim()
    $local = (git rev-parse HEAD).Trim()
    if ($local -ne $remote) {
        git merge --ff-only $remote
        if ($LASTEXITCODE -ne 0) { throw "Fast-forward sync failed; no reset performed" }
    }
    if ((git rev-parse HEAD).Trim() -ne $remote) { throw "Exact remote HEAD verification failed" }
}

if (-not (Test-Path ".\config\atm.json")) {
    Copy-Item ".\config\atm.example.json" ".\config\atm.json"
    Write-Host "Created local config\atm.json"
}
$argsList = @(".\src\atm_v2.py")
if ($Once) { $argsList += "--once" }
if ($Status) { $argsList += "--status" }
if (Get-Command uv -ErrorAction SilentlyContinue) { & uv run --python 3.11 python @argsList; exit $LASTEXITCODE }
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.11 @argsList; exit $LASTEXITCODE }
if (Get-Command python -ErrorAction SilentlyContinue) { & python @argsList; exit $LASTEXITCODE }
throw "Python/uv not found"
