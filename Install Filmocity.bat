@echo off
setlocal
title Filmocity - install dependencies
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if errorlevel 1 goto path_python
py -3.13 packaging\runtime_policy.py
if errorlevel 1 goto python_failed
py -3.13 launcher\bootstrap.py install %*
goto finished
:path_python
python packaging\runtime_policy.py
if errorlevel 1 goto python_failed
python launcher\bootstrap.py install %*
:finished
if errorlevel 1 goto install_failed
echo. & echo Done. Double-click "Filmocity.bat" to launch. & pause
exit /b 0
:python_failed
echo.
echo CPython 3.13.16 or newer on Python 3.13 ^(standard GIL, x64^) is required. Install it from python.org, then retry.
pause
exit /b 1
:install_failed
echo.
echo Install failed. See the error above and packaging\README.md.
echo Supply reviewed FFmpeg and ffprobe in bin or on PATH before retrying.
pause
exit /b 1
