# ================================================================
# NEXUS - Desktop-Verknuepfungen erstellen
# ================================================================

$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$startBat    = Join-Path $projectPath "start.bat"
$desktop     = [Environment]::GetFolderPath("Desktop")
$ws          = New-Object -ComObject WScript.Shell

if (-not (Test-Path $startBat)) {
    Write-Host "[FEHLER] start.bat nicht gefunden: $startBat" -ForegroundColor Red
    exit 1
}

# ---- Alte Shortcuts entfernen ----
foreach ($old in @("NEXUS.lnk", "NEXUS (Tippen).lnk", "NEXUS (Sprache).lnk")) {
    $p = Join-Path $desktop $old
    if (Test-Path $p) { Remove-Item $p -Force }
}

# ---- Hauptverknuepfung: NEXUS (Standard = Textmodus + v=Sprache) ----
$lnk = $ws.CreateShortcut((Join-Path $desktop "NEXUS.lnk"))
$lnk.TargetPath       = "cmd.exe"
$lnk.Arguments        = "/c `"$startBat`""
$lnk.WorkingDirectory = $projectPath
$lnk.WindowStyle      = 1
$lnk.Description      = "NEXUS - Tippen + v=Sprache (empfohlen)"
$lnk.IconLocation     = "%SystemRoot%\System32\shell32.dll,21"
$lnk.Save()
Write-Host "  OK: NEXUS.lnk (Standard - Textmodus + v=Sprache)" -ForegroundColor Green

# ---- Zweite Verknuepfung: Reiner Sprachmodus ----
$lnk2 = $ws.CreateShortcut((Join-Path $desktop "NEXUS (Sprachmodus).lnk"))
$lnk2.TargetPath       = "cmd.exe"
$lnk2.Arguments        = "/c `"$startBat`" voice"
$lnk2.WorkingDirectory = $projectPath
$lnk2.WindowStyle      = 1
$lnk2.Description      = "NEXUS - Nur Mikrofon (reiner Sprachmodus)"
$lnk2.IconLocation     = "%SystemRoot%\System32\shell32.dll,168"
$lnk2.Save()
Write-Host "  OK: NEXUS (Sprachmodus).lnk" -ForegroundColor Green

Write-Host ""
Write-Host "  Doppelklick auf 'NEXUS' = Textmodus mit v+Enter fuer Sprache" -ForegroundColor Cyan
Write-Host ""
