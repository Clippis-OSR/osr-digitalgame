param(
  [string]$Root = "tests\fixtures\replays"
)

$ErrorActionPreference = "Stop"

# Find every replay.json under the fixtures root
$replays = Get-ChildItem -Path $Root -Recurse -Filter "replay.json"

foreach ($r in $replays) {
  $fixtureDir = $r.DirectoryName
  Write-Host "`n=== Regenerating: $fixtureDir ==="

  # Read the existing replay.json and grab commands (and optionally seeds/party if present)
  $json = Get-Content $r.FullName -Raw | ConvertFrom-Json

  if (-not $json.commands -or $json.commands.Count -eq 0) {
    Write-Warning "No commands found in $($r.FullName). Skipping."
    continue
  }

  # Optional fields: keep them if your format includes them; otherwise fall back.
  $party = if ($json.party) { $json.party } else { "default" }
  $diceSeed = if ($json.dice_seed) { [int]$json.dice_seed } else { 11111 }
  $wildSeed = if ($json.wild_seed) { [int]$json.wild_seed } else { 22222 }

  # Re-run each command in order (many fixtures will have exactly 1)
  foreach ($cmd in $json.commands) {
    $cmdJson = ($cmd | ConvertTo-Json -Compress)

    # Call make_fixture for this fixture dir + this command
    python -m scripts.make_fixture $fixtureDir `
      --party $party `
      --dice-seed $diceSeed `
      --wild-seed $wildSeed `
      --cmd $cmdJson

    if ($LASTEXITCODE -ne 0) { throw "make_fixture failed for $fixtureDir" }
  }

  # Bless after regeneration
  python -m scripts.bless_fixture $fixtureDir
  if ($LASTEXITCODE -ne 0) { throw "bless_fixture failed for $fixtureDir" }
}

Write-Host "`nAll fixtures regenerated + blessed."