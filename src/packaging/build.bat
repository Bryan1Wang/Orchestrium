@echo off
rem Double-click or run from anywhere; extra arguments are passed to build.ps1
rem (e.g. build.bat -SmokeTest, build.bat -NoVpo, build.bat -Version 1.2.0).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if "%~1"=="" pause
