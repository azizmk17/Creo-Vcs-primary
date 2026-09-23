@echo off
setlocal

set "JDK_HOME=C:\Program Files\Java\jdk1.7.0_80"
set "PFC_JAR=C:\Program Files\PTC\Creo 3.0\M020\Common Files\text\java\pfc.jar"
set "SOURCE=%~dp0src"
set "OUTPUT=%~dp0classes"
set "DIST=%~dp0dist"

if not exist "%JDK_HOME%\bin\javac.exe" (
    echo ERROR: Java 7 javac.exe was not found at:
    echo %JDK_HOME%\bin\javac.exe
    exit /b 1
)

if not exist "%PFC_JAR%" (
    echo ERROR: Creo 3.0 J-Link pfc.jar was not found at:
    echo %PFC_JAR%
    exit /b 1
)

if not exist "%OUTPUT%" mkdir "%OUTPUT%"
if not exist "%DIST%" mkdir "%DIST%"
del /q "%OUTPUT%\*.class" >nul 2>&1

echo Compiling Nexus PDM J-Link with Java 7...
"%JDK_HOME%\bin\javac.exe" -source 1.7 -target 1.7 -Xlint:all -classpath "%PFC_JAR%" -d "%OUTPUT%" ^
    "%SOURCE%\MiniJson.java" ^
    "%SOURCE%\NexusDialogs.java" ^
    "%SOURCE%\NexusApiException.java" ^
    "%SOURCE%\NexusApiClient.java" ^
    "%SOURCE%\NexusSaveGuard.java" ^
    "%SOURCE%\NexusEditGuard.java" ^
    "%SOURCE%\NexusJLink.java"

if errorlevel 1 (
    echo ERROR: Compilation failed.
    exit /b 1
)

"%JDK_HOME%\bin\jar.exe" cf "%DIST%\nexus-creo-jlink.jar" -C "%OUTPUT%" .
if errorlevel 1 (
    echo ERROR: JAR creation failed.
    exit /b 1
)

echo Build succeeded:
echo   %DIST%\nexus-creo-jlink.jar
exit /b 0
