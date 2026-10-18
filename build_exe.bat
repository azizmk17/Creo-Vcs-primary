@echo off
setlocal DisableDelayedExpansion
title Nexus Folder Build

rem Override with NEXUS_BUILD_PYTHON, or pass --python "C:\path\python.exe".
if /I "%~1"=="--python" if not "%~2"=="" (
    set "NEXUS_BUILD_PYTHON=%~2"
    goto run
)
if defined NEXUS_BUILD_PYTHON goto run
if exist "%USERPROFILE%\miniconda3\envs\pyoccenv\python.exe" (
    set "NEXUS_BUILD_PYTHON=%USERPROFILE%\miniconda3\envs\pyoccenv\python.exe"
    goto run
)
if exist "%USERPROFILE%\anaconda3\envs\pyoccenv\python.exe" (
    set "NEXUS_BUILD_PYTHON=%USERPROFILE%\anaconda3\envs\pyoccenv\python.exe"
    goto run
)
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" (
    set "NEXUS_BUILD_PYTHON=%CONDA_PREFIX%\python.exe"
    goto run
)
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Build Python not found. Set NEXUS_BUILD_PYTHON to your pyoccenv python.exe.
    exit /b 1
)
set "NEXUS_BUILD_PYTHON=python"

:run
"%NEXUS_BUILD_PYTHON%" "%~dp0tools\build_exe.py" %*
set "BUILD_EXIT_CODE=%ERRORLEVEL%"
if not "%BUILD_EXIT_CODE%"=="0" echo [ERROR] Build failed. See the message above and build\folder\logs.
exit /b %BUILD_EXIT_CODE%
