# Build the pinned image and run the benchmark inside it (Windows host).
#   .\scripts\run_benchmark.ps1                                  full run
#   .\scripts\run_benchmark.ps1 -Config configs/quick.yaml       demo run
#   .\scripts\run_benchmark.ps1 -Command preflight               go/no-go only
param(
    [string]$Config = "configs/scenarios.yaml",
    [string]$Cpuset = "2",
    [ValidateSet("run", "preflight")][string]$Command = "run",
    [string]$Extra = ""
)
Set-Location (Split-Path $PSScriptRoot -Parent)

# Docker reports progress on stderr; merging it in cmd keeps Windows PowerShell
# from treating that as an error. Failures are detected from the exit code.
function Invoke-Native([string]$line) {
    cmd /c "$line 2>&1"
    if ($LASTEXITCODE -ne 0) { Write-Error "command failed ($LASTEXITCODE): $line"; exit $LASTEXITCODE }
}

Invoke-Native "docker compose -f docker/compose.yml build"

$sha = (git rev-parse HEAD 2>$null)
if (git status --porcelain 2>$null) { $sha = "$sha-dirty" }
$env:CIPHEROPS_GIT_SHA = $sha
$env:CIPHEROPS_HOST_IMAGE_ID = (docker image inspect cipherops-bench:pinned --format "{{.Id}}")
$env:CIPHEROPS_CPUSET = $Cpuset

if ($Command -eq "preflight") {
    $inner = "python3 -m cipherops.runner preflight --results results"
} else {
    $inner = "python3 -m cipherops.runner run --config $Config $Extra".Trim()
}
Invoke-Native "docker compose -f docker/compose.yml run --rm bench `"$inner`""
