# Overnight P1: law cards -> article index -> embed (resumable). Outside Cursor.
$ErrorActionPreference = "Continue"
$Root = "C:\iraqi-legislation-rag"
$Source = "C:\iraqi-law-rag\sources\laws_master.jsonl"
$Log = Join-Path $Root "cache\overnight_p1.log"
Set-Location $Root
New-Item -ItemType Directory -Force -Path (Join-Path $Root "cache") | Out-Null

# UTF-8 so Arabic titles don't mangle in the log / console.
try { chcp 65001 | Out-Null } catch {}
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

function Write-Log([string]$msg) {
  $line = "$(Get-Date -Format o)  $msg"
  Add-Content -LiteralPath $Log -Value $line -Encoding utf8
  Write-Host $line
}

function Invoke-LoggedPython {
  param(
    [Parameter(Mandatory = $true)][string[]]$PyArgs
  )
  # Append UTF-8 via .NET (no Tee-Object UTF-16; no Add-Content pipe that
  # threw "Stream was not readable" on flushy Python ErrorRecords).
  $utf8 = New-Object System.Text.UTF8Encoding $false
  & python @PyArgs 2>&1 | ForEach-Object {
    $line = $_.ToString()
    [System.IO.File]::AppendAllText($Log, $line + [Environment]::NewLine, $utf8)
    Write-Host $line
  }
  return $LASTEXITCODE
}

Write-Log "=== overnight_p1 START ==="

$venvActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
  Write-Log "FATAL: missing venv at $venvActivate"
  exit 1
}
. $venvActivate

function Load-OpenRouterKey {
  $candidates = @(
    (Join-Path $Root ".env"),
    "C:\iraqi-law-rag\.env"
  )
  foreach ($p in $candidates) {
    if (-not (Test-Path $p)) { continue }
    Get-Content $p -Encoding utf8 | ForEach-Object {
      if ($_ -match '^\s*OPENROUTER_API_KEY\s*=\s*(.+)$') {
        $val = $matches[1].Trim().Trim('"').Trim("'")
        if ($val) {
          $env:OPENROUTER_API_KEY = $val
          Write-Log "Loaded OPENROUTER_API_KEY from $p (len=$($val.Length))"
          return $true
        }
      }
    }
  }
  return $false
}

if (-not $env:OPENROUTER_API_KEY) {
  if (-not (Load-OpenRouterKey)) {
    Write-Log "FATAL: no OPENROUTER_API_KEY"
    exit 1
  }
} else {
  Write-Log "OPENROUTER_API_KEY already in environment (len=$($env:OPENROUTER_API_KEY.Length))"
}

if (-not (Test-Path $Source)) {
  Write-Log "FATAL: missing source $Source"
  exit 1
}

# a) law cards — 8 workers; drop to 4 in build_law_cards if 429s dominate
Write-Log "STEP a: build_law_cards.py --workers 8"
$exitA = Invoke-LoggedPython -PyArgs @("build_law_cards.py", "--workers", "8")
Write-Log "STEP a EXIT=$exitA"

# b) article index
Write-Log "STEP b: build_article_index.py"
$exitB = Invoke-LoggedPython -PyArgs @("build_article_index.py", "--source", $Source)
Write-Log "STEP b EXIT=$exitB"

# c) embed articles
Write-Log "STEP c: embed_articles.py --api"
$exitC = Invoke-LoggedPython -PyArgs @("embed_articles.py", "--api", "--source", $Source)
Write-Log "STEP c EXIT=$exitC"

Write-Log "=== overnight_p1 DONE ==="
