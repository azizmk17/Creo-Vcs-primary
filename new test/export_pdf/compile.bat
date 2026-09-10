@echo off
echo Compiling CreoRibbonButton.java...
javac -cp "D:\appli\Creo 3.0\M120\Common Files\text\java\pfc.jar" CreoRibbonButton.java
if %errorlevel% neq 0 (
    echo Compilation failed!
    pause
    exit /b %errorlevel%
)
echo Compilation successful.
pause
