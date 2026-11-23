$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$processFile = Join-Path $projectRoot '.data\demo-processes.json'
if (Test-Path -LiteralPath $processFile) {
  foreach ($record in (Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json)) {
    $process = Get-Process -Id $record.id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) { Stop-Process -Id $record.id }
  }
  Remove-Item -LiteralPath $processFile
}
