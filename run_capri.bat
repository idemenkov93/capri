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
if not exist .venv\voice_installed_v3.txt (
    echo [Capri] Installing voice module ^(speech recognition + voices + wake phrase^)...
    .venv\Scripts\python -m pip install -r requirements-voice.txt && echo ok> .venv\voice_installed_v3.txt
)
if not exist .venv\system_installed_v1.txt (
    echo [Capri] Installing system commands ^(music, volume, brightness, PC check^)...
    .venv\Scripts\python -m pip install -r requirements-system.txt && echo ok> .venv\system_installed_v1.txt
)
where nvidia-smi >nul 2>nul
if not errorlevel 1 if not exist .venv\gpu_installed_v1.txt (
    echo [Capri] NVIDIA GPU found: installing CUDA libraries for fast speech recognition ^(about 1.3 GB, once^)...
    .venv\Scripts\python -m pip install -r requirements-gpu.txt && echo ok> .venv\gpu_installed_v1.txt
)
if not exist data\models\vosk-model-small-ru-0.22\am (
    echo [Capri] Downloading the wake phrase model ^(Russian, about 45 MB^)...
    if not exist data\models mkdir data\models
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip' -OutFile 'data\models\vosk-ru.zip'; Expand-Archive -Force 'data\models\vosk-ru.zip' 'data\models'; Remove-Item 'data\models\vosk-ru.zip'"
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
start "" .venv\Scripts\pythonw -m capri
