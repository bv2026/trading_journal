param(
  [switch]$IncludePorts
)

$ErrorActionPreference = "SilentlyContinue"

function Stop-ByCommandLinePattern {
  param([string[]]$Patterns)
  Get-CimInstance Win32_Process |
    Where-Object {
      $cmd = $_.CommandLine
      if (-not $cmd) { return $false }
      foreach ($p in $Patterns) {
        if ($cmd -match $p) { return $true }
      }
      return $false
    } |
    ForEach-Object {
      Write-Host ("Stopping PID {0} ({1})" -f $_.ProcessId, $_.Name)
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Killing trading-journal zombie processes..."

# API / UI watchers launched by the journal CLI
Stop-ByCommandLinePattern -Patterns @(
  "src\.api\.main",
  "uvicorn.*8000",
  "next.*dev.*-p.*3000",
  "npm.*run.*dev",
  "node.*next"
)

if ($IncludePorts) {
  foreach ($port in @(3000, 8000)) {
    Get-NetTCPConnection -LocalPort $port -State Listen |
      Select-Object -ExpandProperty OwningProcess -Unique |
      ForEach-Object {
        Write-Host ("Stopping port listener PID {0} on :{1}" -f $_, $port)
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
      }
  }
}

Write-Host "Done. Current listeners on 3000/8000:"
Get-NetTCPConnection -State Listen -LocalPort 3000,8000 |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  Format-Table -AutoSize

