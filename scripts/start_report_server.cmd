@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" "scripts\report_server.py" --host 0.0.0.0 --port 8876
