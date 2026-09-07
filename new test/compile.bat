@echo off
echo Compiling CreoAsyncTest.java with pfcasync.jar...
javac -cp "D:\appli\Creo 3.0\M120\Common Files\text\java\pfcasync.jar" CreoAsyncTest.java
if %errorlevel% neq 0 (
    echo Compilation failed!
    pause
    exit /b %errorlevel%
)
echo Compilation successful.
pause
