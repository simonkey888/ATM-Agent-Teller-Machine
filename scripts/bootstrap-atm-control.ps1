$ErrorActionPreference = "Stop"
$ExpectedRoot = "C:\Users\Simon\ATM\ATM-Agent-Teller-Machine-agent-atm-v1"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ResolvedRoot = (Resolve-Path $Root).Path
if ($ResolvedRoot.TrimEnd('\') -ne $ExpectedRoot.TrimEnd('\')) { throw "ATM repo path mismatch. Expected $ExpectedRoot; got $ResolvedRoot" }
Set-Location $ResolvedRoot
foreach ($cmd in @("git", "gh", "powershell")) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found" } }
if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.11 runtime not found" }
& gh auth status 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw "GitHub CLI is not authenticated" }
$branch = "agent/atm-v1"
git fetch origin $branch
if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
$dirty = git status --porcelain --untracked-files=no
if ($dirty) { throw "Tracked local changes present; refusing automatic sync" }
$remote = (git rev-parse "origin/$branch").Trim(); $local = (git rev-parse HEAD).Trim()
if ($local -ne $remote) { git merge --ff-only $remote; if ($LASTEXITCODE -ne 0) { throw "Fast-forward sync failed" } }
$head = (git rev-parse HEAD).Trim()
$TaskName = "ATM-GitHub-Control"
$Sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Runner = Join-Path $ResolvedRoot "scripts\run-atm-control.ps1"
$Template = Get-Content (Join-Path $ResolvedRoot "scripts\atm-control-task.xml.template") -Raw
$Xml = $Template.Replace("__USER_SID__", $Sid).Replace("__RUNNER_PS1__", [System.Security.SecurityElement]::Escape($Runner)).Replace("__REPO_ROOT__", [System.Security.SecurityElement]::Escape($ResolvedRoot))
Register-ScheduledTask -TaskName $TaskName -Xml $Xml -Force | Out-Null
function Invoke-ControllerCheck {
    if (Get-Command uv -ErrorAction SilentlyContinue) { & uv run --python 3.11 python .\src\atm_control.py --is-running }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3.11 .\src\atm_control.py --is-running }
    else { & python .\src\atm_control.py --is-running }
    return $LASTEXITCODE
}
if ((Invoke-ControllerCheck) -ne 0) { Start-ScheduledTask -TaskName $TaskName; for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 500; if ((Invoke-ControllerCheck) -eq 0) { break } } }
if ((Invoke-ControllerCheck) -ne 0) { throw "Controller did not acquire singleton lock" }
$Task = Get-ScheduledTask -TaskName $TaskName
if (-not $Task) { throw "Scheduled Task missing after registration" }
Write-Host "ATM_CONTROL_BOOTSTRAP_OK"; Write-Host "HEAD=$head"; Write-Host "CONTROL_ISSUE=4"; Write-Host "TASK=$TaskName"; Write-Host "CONTROLLER_ACTIVE=YES"
