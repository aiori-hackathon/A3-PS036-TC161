# Build the pinned image and run the benchmark inside it (Windows host).
#   .\scripts\run_benchmark.ps1                                  full run
#   .\scripts\run_benchmark.ps1 -Config configs/quick.yaml       demo run
#   .\scripts\run_benchmark.ps1 -Command preflight               go/no-go only
param(
    [string]$Config = "configs/scenarios.yaml",
    [string]$Cpuset = "2",
    [ValidateSet("run", "preflight")][string]$Command = "run",
    [string]$Extra = "",
    # ecosystem/stacks/<Stack>.env is the single source of the pinned versions (the live lab uses the same file)
    [string]$Stack = $(if ($env:CIPHEROPS_STACK) { $env:CIPHEROPS_STACK } else { "latest" })
)
Set-Location (Split-Path $PSScriptRoot -Parent)
$EnvFile = "ecosystem/stacks/$Stack.env"
if (-not (Test-Path $EnvFile)) { Write-Error "unknown stack '$Stack': no $EnvFile"; exit 1 }

# Docker reports progress on stderr; merging it in cmd keeps Windows PowerShell
# from treating that as an error. Failures are detected from the exit code.
function Invoke-Native([string]$line) {
    cmd /c "$line 2>&1"
    if ($LASTEXITCODE -ne 0) { Write-Error "command failed ($LASTEXITCODE): $line"; exit $LASTEXITCODE }
}

# Build the pinned image here, or set CIPHEROPS_SKIP_BUILD=1 to run the one already on this host.
if ($env:CIPHEROPS_SKIP_BUILD -eq "1") {
    docker image inspect "cipherops-bench:$Stack" *> $null
    if ($LASTEXITCODE -ne 0) { Write-Error "cipherops-bench:$Stack is not on this host; build or pull it first."; exit 1 }
} else {
    Invoke-Native "docker compose --env-file $EnvFile -f docker/build.yml build bench"
}

$sha = (git rev-parse HEAD 2>$null)
if (git status --porcelain 2>$null) { $sha = "$sha-dirty" }
$env:CIPHEROPS_GIT_SHA = $sha
$env:CIPHEROPS_HOST_IMAGE_ID = (docker image inspect "cipherops-bench:$Stack" --format "{{.Id}}")
$env:CIPHEROPS_CPUSET = $Cpuset

if ($Command -eq "preflight") {
    $inner = "python3 -m cipherops.runner preflight --results results"
} else {
    $inner = "python3 -m cipherops.runner run --config $Config $Extra".Trim()
}
Invoke-Native "docker compose --env-file $EnvFile -f docker/compose.yml run --rm bench `"$inner`""
