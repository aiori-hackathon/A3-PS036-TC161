# Live mode on a finished run: real client, real NSD, tc-netem network profiles, pcaps.
#   .\scripts\run_live.ps1 -Run 20261007T045213Z-quick-demo
#   .\scripts\run_live.ps1 -Run <run> -Profiles lan -Udp 1232 -Repeats 1     quick look
param(
    [Parameter(Mandatory = $true)][string]$Run,
    [string]$Datasets = "",
    [string]$Profiles = "lan,wan,lossy",
    [string]$Udp = "1232,4096",
    [int]$Repeats = 3,
    [string]$Cpuset = "2"
)
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:CIPHEROPS_CPUSET = $Cpuset
$inner = "python3 -m cipherops.runner live --run results/runs/$Run --profiles $Profiles --udp $Udp --repeats $Repeats"
if ($Datasets) { $inner += " --datasets $Datasets" }
cmd /c "docker compose -f docker/compose.yml run --rm live `"$inner`" 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Error "live run failed ($LASTEXITCODE)"; exit $LASTEXITCODE }
