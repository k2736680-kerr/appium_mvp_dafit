@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" "scripts\nightly_run.py" --notify auto
