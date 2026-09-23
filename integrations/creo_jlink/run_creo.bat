@echo off
setlocal

set "JAVA_EXE=C:\PROGRA~1\Java\jre7\bin\java.exe"
set "PFC_JAR=C:\Program Files\PTC\Creo 3.0\M020\Common Files\text\java\pfc.jar"
set "CREO_LAUNCHER=C:\Program Files\PTC\Creo 3.0\M020\Parametric\bin\parametric.bat"
set "BRIDGE_FILE=%LOCALAPPDATA%\CreoVCS\bridge.json"
set "PTC_WF_ROOT=%LOCALAPPDATA%\CreoVCS\ptc-wf-root"

if not exist "%JAVA_EXE%" (
    echo ERROR: Java 7 was not found at:
    echo %JAVA_EXE%
    pause
    exit /b 1
)

if not exist "%PFC_JAR%" (
    echo ERROR: Creo 3.0 J-Link pfc.jar was not found at:
    echo %PFC_JAR%
    pause
    exit /b 1
)

if not exist "%CREO_LAUNCHER%" (
    echo ERROR: Creo 3.0 launcher was not found at:
    echo %CREO_LAUNCHER%
    pause
    exit /b 1
)

if not exist "%~dp0classes\NexusJLink.class" (
    echo ERROR: Nexus J-Link is not compiled. Run compile.bat first.
    pause
    exit /b 1
)

if not exist "%BRIDGE_FILE%" (
    echo WARNING: Nexus is not currently exposing its Creo bridge.
    echo Start Nexus, sign in, and select a product version before using the Nexus PDM menu.
    echo.
)

if not exist "%PTC_WF_ROOT%" mkdir "%PTC_WF_ROOT%"

set "PRO_JAVA_COMMAND=%JAVA_EXE%"
set "CLASSPATH=%~dp0classes;%PFC_JAR%"

cd /d "%~dp0"
echo Starting Creo with Nexus PDM J-Link from %CD%...
call "%CREO_LAUNCHER%"
