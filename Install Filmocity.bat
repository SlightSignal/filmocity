@echo off
title Filmocity - install dependencies
cd /d "%~dp0"
where py >nul 2>nul && (py -3 launcher\bootstrap.py install %*) || (python launcher\bootstrap.py install %*)
if errorlevel 1 (echo. & echo Install failed. Python 3.10+ is required: https://www.python.org/downloads/  (tick "Add python.exe to PATH") & pause & exit /b 1)
echo. & echo Done. Double-click "Filmocity.bat" to launch. & pause
