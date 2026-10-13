@echo off
rem Double-click to open the trading bot control panel.
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Python environment not found. Run the setup steps in SETUP_MT5.md first.
  pause
  exit /b 1
)
venv\Scripts\python.exe app.py
pause
