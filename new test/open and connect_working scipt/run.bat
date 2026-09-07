@echo off
echo Setting up PTC environment paths...

REM 1. Chemin vers l'exécutable de messagerie de Creo 3.0
set PRO_COMM_MSG_EXE=D:\appli\Creo 3.0\M120\Common Files\x86e_win64\obj\pro_comm_msg.exe

REM 2. Ajout de la DLL native pfcasyncmt au PATH de Windows pour Java
set PATH=D:\appli\Creo 3.0\M120\Common Files\x86e_win64\lib;%PATH%

echo Running CreoAsyncTest...
java -cp ".;D:\appli\Creo 3.0\M120\Common Files\text\java\pfcasync.jar" CreoAsyncTest
pause
