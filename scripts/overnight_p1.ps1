# Overnight P1: law cards -> article index -> embed (resumable). Outside Cursor.
$ErrorActionPreference = "Continue"
$Root = "C:\iraqi-legislation-rag"
$Source = "C:\iraqi-law-rag\sources\laws_master.jsonl"
$Log = Join-Path $Root "cache\overnight_p1.log"
Set-Location $Root
New-Item -ItemType Directory -Force -Path (Join-Path $Root "cache") | Out-Null

function Write-Log([string]$msg) {
  $line = "$(Get-Date -Format o)  $msg"
  Add-Content -Path $Log -Value $line -Encoding utf8
  Write-Host $line
}

Write-Log "=== overnight_p1 START ==="

$venvActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
  Write-Log "FATAL: missing venv at $venvActivate"
  exit 1
}
. $venvActivate

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

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

# a) law cards
Write-Log "STEP a: build_law_cards.py"
& python build_law_cards.py 2>&1 | ForEach-Object { Add-Content -Path $Log -Value $_ -Encoding utf8; $_ }
Write-Log "STEP a EXIT=$LASTEXITCODE"

# b) article index
Write-Log "STEP b: build_article_index.py"
& python build_article_index.py --source $Source 2>&1 | ForEach-Object { Add-Content -Path $Log -Value $_ -Encoding utf8; $_ }
Write-Log "STEP b EXIT=$LASTEXITCODE"

# c) embed articles
Write-Log "STEP c: embed_articles.py --api"
& python embed_articles.py --api --source $Source 2>&1 | ForEach-Object { Add-Content -Path $Log -Value $_ -Encoding utf8; $_ }
Write-Log "STEP c EXIT=$LASTEXITCODE"

Write-Log "=== overnight_p1 DONE ==="
