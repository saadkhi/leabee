@echo off
rem One-click start on Windows: installs dependencies once, then runs the dashboard with
rem background collection every 2 hours. Open http://127.0.0.1:8765
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:8765
python -m leabee serve --auto-collect 120
pause
