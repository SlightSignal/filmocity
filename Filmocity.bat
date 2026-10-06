@echo off
setlocal
title Filmocity
cd /d "%~dp0"
rem Preserve UTF-8 text behavior for all source entry points.
set PYTHONUTF8=1
where py >nul 2>nul
if errorlevel 1 goto path_python
py -3.13 packaging\runtime_policy.py
if errorlevel 1 goto python_failed
py -3.13 launcher\bootstrap.py %*
goto finished
:path_python
python packaging\runtime_policy.py
if errorlevel 1 goto python_failed
python launcher\bootstrap.py %*
:finished
if errorlevel 1 goto launch_failed
exit /b 0
:python_failed
echo.
echo CPython 3.13.16 or newer on Python 3.13 ^(standard GIL, x64^) is required. Install it from python.org, then retry.
pause
exit /b 1
:launch_failed
echo.
echo Filmocity could not start. See the error above and packaging\README.md.
pause
exit /b 1
