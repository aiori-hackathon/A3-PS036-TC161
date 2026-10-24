# Print a run's report.html to report.pdf with headless Microsoft Edge or Chrome.
#   .\scripts\make_pdf.ps1 results\runs\<run-id>
param([Parameter(Mandatory = $true)][string]$RunDir)
$ErrorActionPreference = "Stop"
$html = Resolve-Path (Join-Path $RunDir "report.html")
$pdf = Join-Path (Resolve-Path $RunDir) "report.pdf"
$browsers = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
)
$browser = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Edge or Chrome not found; open report.html and print to PDF manually." }
$ErrorActionPreference = "Continue"  # the browser logs harmless errors on stderr
& $browser --headless --disable-gpu --no-pdf-header-footer "--print-to-pdf=$pdf" "file:///$($html -replace '\\','/')" 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
Start-Sleep -Seconds 3
if (-not (Test-Path $pdf)) { throw "PDF was not produced." }
Write-Output $pdf
