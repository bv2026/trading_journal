param(
  [switch]$Apply
)

$ErrorActionPreference = "SilentlyContinue"

$patterns = @(
  "trading-journal\\src\\mcp_server\.py",
  "futures-trading-system\\mcp_server\.py",
  "tradeguard-agent\\mcp_server\.py",
  "bullstrangle-mcp-server\.exe",
  "newsletter-mcp(\.server|\.exe)",
  "coinbase_derivatives_mcp",
  "schwab_mcp\.server",
  "webull-openapi",
  "chrome-devtools-mcp",
  "src\.api\.main",
  "uvicorn.*8000",
  "next.*dev.*-p.*3000",
  "npm.*run.*dev",
  "node.*next",
  "multiprocessing\.spawn"
)

$candidates = Get-CimInstance Win32_Process | Where-Object {
  $name = $_.Name
  if ($name -notmatch '^(python|node|npm|npx)(\.exe)?$') { return $false }
  $cmd = $_.CommandLine
  if (-not $cmd) { return $false }
  foreach ($p in $patterns) {
    if ($cmd -match $p) { return $true }
  }
  return $false
}

if (-not $candidates) {
  Write-Host "No matching unneeded processes found."
  exit 0
}

Write-Host "Matched processes:"
$candidates |
  Select-Object ProcessId, Name, CommandLine |
  Sort-Object Name, ProcessId |
  Format-Table -AutoSize

if (-not $Apply) {
  Write-Host ""
  Write-Host "Preview only. Re-run with -Apply to kill."
  exit 0
}

Write-Host ""
Write-Host "Killing matched processes..."
$candidates | ForEach-Object {
  Write-Host ("Stopping PID {0} ({1})" -f $_.ProcessId, $_.Name)
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Remaining python/node processes:"
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^(python|node|npm|npx)(\.exe)?$' } |
  Select-Object ProcessId, Name, CommandLine |
  Sort-Object Name, ProcessId |
  Format-Table -AutoSize

