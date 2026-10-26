@echo off
cd /d "%~dp0"
if not exist .venv (
    echo [Capri] First run: creating virtual environment...
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
start "" .venv\Scripts\pythonw -m capri
