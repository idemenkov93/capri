@echo off
cd /d "%~dp0"
rem Capri self-test: checks every part and writes data\selftest.txt
.venv\Scripts\python -m capri.selftest
pause
