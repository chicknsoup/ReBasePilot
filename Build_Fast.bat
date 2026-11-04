@echo off
cd /d "%~dp0"
taskkill /F /IM BasePilot.exe >nul 2>&1
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m PyInstaller BasePilot.spec --distpath build_dist --workpath build_work --noconfirm
echo.
echo Build hoan tat: "%~dp0build_dist\BasePilot\BasePilot.exe"
