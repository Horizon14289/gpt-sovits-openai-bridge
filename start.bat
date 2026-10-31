@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist config.json (
  echo [!] config.json not found - copy config.example.json first
  pause
  exit /b 1
)
python bridge.py
pause
