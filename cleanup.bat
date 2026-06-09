@echo off
REM ================================================================
REM NEXUS - Aufraeum-Skript
REM Loescht Streu-Dateien, die beim ersten install.bat-Lauf entstanden
REM sind (pip-Ausgaben wurden faelschlicherweise in Versionsnummer-Dateien
REM umgeleitet, weil >= als CMD-Redirect interpretiert wurde).
REM ================================================================
cd /d "%~dp0"

echo Loesche fehlerhafte pip-Log-Dateien ...
for %%F in (0.4.6 1.26.0 2.5.2 2.90 3.10.0 6.1.10) do (
    if exist "%%F" (
        del /f "%%F"
        echo   Geloescht: %%F
    )
)

echo.
echo Fertig. Bitte jetzt install.bat erneut ausfuehren.
pause
