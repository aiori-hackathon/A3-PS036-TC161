# Live mode on a finished run: real client, real NSD, tc-netem network profiles, pcaps.
#   .\scripts\run_live.ps1 -Run <run-folder>
#   .\scripts\run_live.ps1 -Run <run> -Profiles lan -Udp 1232 -Repeats 1     quick look
param(
    [Parameter(Mandatory = $true)][string]$Run,
    [string]$Datasets = "",
    [string]$Profiles = "lan,wan,lossy",
    [string]$Udp = "1232,4096",
    [int]$Repeats = 3,
    [string]$Cpuset = "2",
    [string]$Stack = $(if ($env:CIPHEROPS_STACK) { $env:CIPHEROPS_STACK } else { "latest" })   # as used for the run
)
Set-Location (Split-Path $PSScriptRoot -Parent)
$EnvFile = "ecosystem/stacks/$Stack.env"
if (-not (Test-Path $EnvFile)) { Write-Error "unknown stack '$Stack': no $EnvFile"; exit 1 }
$env:CIPHEROPS_CPUSET = $Cpuset
$inner = "python3 -m cipherops.runner live --run results/runs/$Run --profiles $Profiles --udp $Udp --repeats $Repeats"
if ($Datasets) { $inner += " --datasets $Datasets" }
cmd /c "docker compose --env-file $EnvFile -f docker/compose.yml run --rm live `"$inner`" 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Error "live run failed ($LASTEXITCODE)"; exit $LASTEXITCODE }
