@echo off
setlocal

call "%~dp0compile.bat"
if errorlevel 1 (
    echo.
    echo Build stopped because compilation failed.
    pause
    exit /b 1
)

call "%~dp0run_creo.bat"
exit /b %ERRORLEVEL%
