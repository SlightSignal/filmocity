@echo off
title Filmocity
cd /d "%~dp0"

rem UTF-8 MODE, added 2026-09-07. Without it Filmocity serves "Internal
rem Server Error" on this machine and nothing else. backend/server.py:294
rem is `open(os.path.join(FRONT,"index.html")).read()` with no encoding,
rem so Windows decodes a UTF-8 index.html as cp1252 and dies on byte 0x8f
rem at offset 52938. It is not one line: 66 text opens across backend/
rem and launcher/ take the platform default, which is UTF-8 on the mac
rem and linux this was written on and cp1252 here.
rem
rem PYTHONUTF8=1 is the supported switch for exactly this (PEP 540) and
rem fixes all 66 at once without touching their code -- which also means
rem an upstream update overwrites nothing. Python 3.15 makes it the
rem default, so this line is temporary by design.
set PYTHONUTF8=1
where py >nul 2>nul && (py -3 launcher\bootstrap.py %*) || (python launcher\bootstrap.py %*)
if errorlevel 1 (echo. & echo Filmocity could not start. Run "Install Filmocity.bat" first, or install Python 3.10+ from python.org. & pause)
