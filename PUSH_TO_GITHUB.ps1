# NEXUS - Push to GitHub
# Dieses Script einmal ausfuehren nachdem das neue Repo auf GitHub erstellt wurde

$repoPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoPath

Write-Host ""
Write-Host "NEXUS - GitHub Push" -ForegroundColor Cyan
Write-Host "===================" -ForegroundColor Cyan
Write-Host ""

# Remote pruefen / setzen
$remotes = git remote 2>$null
if ($remotes -notcontains "origin") {
    Write-Host "[1/2] Remote hinzufuegen..." -ForegroundColor Yellow
    git remote add origin https://github.com/nexus-osint-cyber/nexus.git
    Write-Host "      origin -> https://github.com/nexus-osint-cyber/nexus.git" -ForegroundColor Green
} else {
    $currentUrl = git remote get-url origin
    Write-Host "[1/2] Remote bereits vorhanden: $currentUrl" -ForegroundColor Green
}

Write-Host ""
Write-Host "[2/2] Push zu GitHub..." -ForegroundColor Yellow
Write-Host "      (GitHub fragt ggf. nach Benutzername + Token)" -ForegroundColor Gray
Write-Host ""

git push -u origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  ERFOLG! NEXUS ist sauber auf GitHub." -ForegroundColor Green
    Write-Host "  https://github.com/nexus-osint-cyber/nexus" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Fehler beim Push. Moegliche Ursachen:" -ForegroundColor Red
    Write-Host "  - Repo noch nicht auf GitHub erstellt?" -ForegroundColor Yellow
    Write-Host "  - Falsches Passwort / Token abgelaufen?" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Repo erstellen unter: https://github.com/new" -ForegroundColor Cyan
    Write-Host "  Name: nexus  |  NICHTS anhaeken  |  Create repository" -ForegroundColor Cyan
    git remote -v
}

Write-Host ""
Read-Host "Enter druecken zum Beenden"
