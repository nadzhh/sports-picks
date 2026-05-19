@echo off
cd /d "%~dp0"
title Sports Picks
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

:menu
cls
echo ============================================
echo   SPORTS PICKS  (api-football)
echo ============================================
echo.
echo   1. Ouvrir le site (picks deja generes)
echo   2. Rafraichir les donnees + generer le site
echo   3. Quitter
echo.
set /p choix=Choix [1-3] :

if "%choix%"=="1" goto open
if "%choix%"=="2" goto refresh
if "%choix%"=="3" exit
goto menu

:open
if not exist "index.html" (
    echo.
    echo X index.html introuvable. Lance d'abord l'option 2.
    pause
    goto menu
)
start "" "index.html"
exit

:refresh
echo.
echo ============================================
echo   Refresh complet (~1-2 min)
echo ============================================
echo.
echo Pipeline :
echo   1. Fixtures + odds + h2h    (api-football)
echo   2. Stats joueurs            (top scorers / assists)
echo   3. Generation du site
echo   4. Push GitHub Pages
echo.

call venv\Scripts\activate

python run_all.py
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
    echo ============================================
    echo OK - Ouverture du site...
    echo ============================================
    start "" "index.html"
) else (
    echo ============================================
    echo X Pipeline echouee ^(code %RC%^).
    echo   Tes donnees existantes sont preservees.
    echo   Option 1 pour ouvrir la version precedente.
    echo ============================================
)

echo.
echo Appuie sur une touche...
pause > nul
goto menu
