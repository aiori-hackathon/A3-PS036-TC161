# Serve a run's signed zone from NSD (one port per mode) so the dashboard can send real queries.
#   .\scripts\live_server.ps1 -Run 20261007T045213Z-quick-demo
#   .\scripts\live_server.ps1 -Run <run> -Dataset small -Profile wan
# Ports 5301.. (one per mode) and 5310 (MTL full signatures) on 127.0.0.1. Stop with Ctrl+C.
param(
    [Parameter(Mandatory = $true)][string]$Run,
    [string]$Dataset = "",
    [ValidateSet("lan", "wan", "lossy")][string]$Profile = "lan",
    [string]$Cpuset = "2"
)
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:CIPHEROPS_CPUSET = $Cpuset
$inner = "python3 -m cipherops.runner serve --run results/runs/$Run --profile $Profile"
if ($Dataset) { $inner += " --dataset $Dataset" }
docker compose -f docker/compose.yml run --rm --service-ports --name cipherops-live-server serve "$inner"
