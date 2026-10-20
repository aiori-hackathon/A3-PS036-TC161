<#
Ecosystem lab runner (Windows). Usage:
  powershell -ExecutionPolicy Bypass -File ecosystem/scripts/eco.ps1 <command> [-Config configs/lab.yaml] [-Stack draft06] [extra args]

Commands (in order):
  build     build the NSD, Unbound and tools images for the stack
  prepare   sign every scenario's zones (frozen + hashed) and write the compose file
  up        start the hierarchy (root, TLD and SLD servers, Unbound, client)
  smoke     resolve a few names per scenario and require validated (AD) answers
  run       full trials; extra args go to "ecolab run" (e.g. --scenarios mtl,ecdsa --profiles lan)
  down      stop and remove the lab containers
  shell     open a shell in the client container
#>
param(
    [Parameter(Position = 0)][ValidateSet("build", "prepare", "up", "smoke", "run", "down", "shell")][string]$Command,
    [string]$Config = "configs/lab.yaml",
    [string]$Stack = "draft06",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Stop"
$Eco = Split-Path -Parent $PSScriptRoot
$Repo = Split-Path -Parent $Eco
$Lab = (Select-String -Path (Join-Path $Eco $Config) -Pattern '^name:\s*(\S+)').Matches[0].Groups[1].Value
$Compose = Join-Path $Eco "work/$Lab-$Stack/compose.yml"
$env:CIPHEROPS_GIT_SHA = (git -C $Repo rev-parse --short HEAD 2>$null)
$Args2 = if ($Rest) { $Rest } else { @() }

function Need-Compose {
    if (-not (Test-Path $Compose)) { throw "No $Compose yet - run 'prepare' first." }
}

switch ($Command) {
    "build" {
        docker compose -f "$Eco/docker/build.yml" --env-file "$Eco/stacks/$Stack.env" build
    }
    "prepare" {
        docker run --rm --network none -v "${Eco}:/eco" -v "${Repo}/src:/work/src:ro" "cipherops-eco-tools:$Stack" `
            "python3 -m ecolab prepare $Config $($Args2 -join ' ')"
    }
    "up" { Need-Compose; docker compose -f $Compose up -d }
    "smoke" { Need-Compose; docker compose -f $Compose exec client python3 -m ecolab smoke $Config @Args2 }
    "run" { Need-Compose; docker compose -f $Compose exec client python3 -m ecolab run $Config @Args2 }
    "down" { Need-Compose; docker compose -f $Compose down }
    "shell" { Need-Compose; docker compose -f $Compose exec client bash }
}
exit $LASTEXITCODE
