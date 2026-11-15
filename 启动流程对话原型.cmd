@echo off
cd /d "%~dp0"
if exist "D:\miniconda\pythonw.exe" (
    start "" "D:\miniconda\pythonw.exe" workflow_studio.py
) else (
    python workflow_studio.py
)
