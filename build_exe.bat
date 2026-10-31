@echo off
rem Personal build: Capri.exe + installer WITH your animations from assets\custom.
rem Only for yourself - do not upload the result anywhere (the pictures are not yours to share).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [Capri] Run run_capri.bat once first - it creates .venv with everything Capri needs.
    pause & exit /b 1
)
echo [Capri] Installing PyInstaller...
.venv\Scripts\python -m pip install --upgrade "pyinstaller>=6.16" >nul || (pause & exit /b 1)
for /f %%v in ('.venv\Scripts\python -c "import capri;print(capri.__version__)"') do set CAPRI_VERSION=%%v
set CAPRI_PERSONAL=1
.venv\Scripts\python packaging\make_icon.py >nul
echo [Capri] Building Capri.exe %CAPRI_VERSION% (a few minutes)...
.venv\Scripts\pyinstaller --noconfirm --clean --log-level WARN packaging\capri.spec --distpath dist --workpath build || (pause & exit /b 1)

echo [Capri] Checking the build...
start "" /wait dist\Capri\Capri.exe --check=%~dp0dist\check.txt
findstr /B "FAIL" dist\check.txt && (echo [Capri] Something is missing in the build - see dist\check.txt & pause & exit /b 1)
echo [Capri] Build OK.

set ISCC=
where iscc >nul 2>nul && set ISCC=iscc
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [Capri] Inno Setup not found - installing it for this user...
    winget install --id JRSoftware.InnoSetup -e --scope user --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
    echo [Capri] No Inno Setup - the program is ready in dist\Capri\Capri.exe ^(without installer^).
    pause & exit /b 0
)
%ISCC% /Q /DAppVersion=%CAPRI_VERSION% /DSuffix=-personal packaging\capri.iss || (pause & exit /b 1)
echo.
echo [Capri] Done: dist\CapriSetup-%CAPRI_VERSION%-personal.exe
explorer dist
pause
