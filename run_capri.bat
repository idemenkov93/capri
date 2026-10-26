@echo off
cd /d "%~dp0"

rem Find Python: prefer the "py" launcher (works even when python is not in PATH)
set PY=
where py >nul 2>nul && set PY=py
if not defined PY (
    python --version >nul 2>nul && set PY=python
)
if not defined PY (
    echo [Capri] Python not found. Install it from https://www.python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist .venv (
    echo [Capri] First run: creating virtual environment...
    %PY% -m venv .venv || (pause & exit /b 1)
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt || (echo [Capri] Failed to install requirements. & pause & exit /b 1)
)
if not exist .venv\voice_installed_v2.txt (
    echo [Capri] Installing voice module ^(speech recognition + voices^)...
    .venv\Scripts\python -m pip install -r requirements-voice.txt && echo ok> .venv\voice_installed_v2.txt
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
start "" .venv\Scripts\pythonw -m capri
