@echo off
cd /d "%~dp0"
if exist "D:\miniconda\pythonw.exe" (
    start "" "D:\miniconda\pythonw.exe" main.py
) else (
    python main.py
)
