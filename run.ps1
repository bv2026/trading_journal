param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $repo ".venv313"
$py = Join-Path $venv "Scripts\python.exe"
$req = Join-Path $repo "requirements.txt"

if (-not (Test-Path $py)) {
  Write-Host "Creating .venv313 with Python 3.13..."
  py -3.13 -m venv $venv
  & $py -m pip install -U pip
  if (Test-Path $req) {
    Write-Host "Installing requirements..."
    & $py -m pip install -r $req
  }
}

if ($Args.Count -eq 0) {
  & $py -m src.journal_cli
} else {
  & $py @Args
}

