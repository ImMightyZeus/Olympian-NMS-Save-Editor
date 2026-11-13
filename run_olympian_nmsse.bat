@echo off
setlocal EnableExtensions
title Olympian NMS Save Editor
cd /d "%~dp0"
set "LOG=%~dp0olympian_log.txt"
set "OLY_DIR=%~dp0"
echo ==== %date% %time% ==== > "%LOG%"
set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python313"

rem --- 0. Entorno ya preparado y sano? ----------------------------------------------
if not exist ".venv\Scripts\python.exe" goto findpy
".venv\Scripts\python.exe" -c "import encodings, olympian" >> "%LOG%" 2>&1
if not errorlevel 1 goto run
echo  El entorno de Olympian estaba danado. Lo vuelvo a preparar...
rmdir /s /q ".venv"

rem --- 1. Buscar un Python 3.11+ que funcione de verdad ----------------------------
:findpy
set "PY="
call :try py -3.13
if not defined PY call :try py -3
if not defined PY call :try python
if not defined PY call :try "%PYDIR%\python.exe"
if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PY goto venv

rem Hay una instalacion de Python 3.13 pero no arranca: necesita reparacion
if exist "%PYDIR%\python.exe" goto broken

echo.
echo  Olympian necesita Python (gratis, de python.org). Se instalara solo para tu usuario.
echo  Si Windows pregunta, acepta. Esto pasa solo la primera vez.
echo.
winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
call :try "%PYDIR%\python.exe"
if defined PY goto venv
echo.
echo  No pude instalar Python automaticamente.
echo  Instala "Python 3.13" desde python.org o la Microsoft Store y vuelve a abrir este archivo.
echo  Detalles en olympian_log.txt
pause
exit /b 1

:broken
echo.
echo  Tu Python 3.13 esta instalado pero danado (le faltan archivos y no arranca).
echo  Para arreglarlo:
echo    1. Abre Configuracion ^> Aplicaciones ^> Aplicaciones instaladas
echo    2. Busca "Python 3.13", pulsa los tres puntos ^> Modificar ^> Repair
echo    3. Cuando termine, vuelve a abrir run_olympian_nmsse.bat
echo  Si no aparece en la lista, borra la carpeta:
echo    %PYDIR%
echo  y vuelve a abrir run_olympian_nmsse.bat (instalara Python de nuevo).
echo. >> "%LOG%"
echo BROKEN PYTHON: %PYDIR% >> "%LOG%"
"%PYDIR%\python.exe" -c "import sys" >> "%LOG%" 2>&1
dir "%PYDIR%" >> "%LOG%" 2>&1
pause
exit /b 1

rem --- 2. Preparar el entorno (solo la primera vez) --------------------------------
:venv
echo  Usando Python: %PY% >> "%LOG%"
echo  Preparando Olympian por primera vez...
%PY% -m venv .venv >> "%LOG%" 2>&1
if not exist ".venv\Scripts\python.exe" goto venvfail
rem lz4 acelera la lectura; si no se instala, Olympian usa su lector propio
".venv\Scripts\python.exe" -m pip install --no-index --find-links "%~dp0wheels" lz4 >> "%LOG%" 2>&1

:run
rem Acceso directo con el icono de Olympian (los .bat no pueden tener icono propio).
rem Se crea junto a este archivo; puedes copiarlo al escritorio o anclarlo a la barra de tareas.
if exist "%~dp0olympian.ico" if not exist "%~dp0Olympian NMS Save Editor.lnk" powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:OLY_DIR+'Olympian NMS Save Editor.lnk');$s.TargetPath=$env:OLY_DIR+'run_olympian_nmsse.bat';$s.WorkingDirectory=$env:OLY_DIR;$s.IconLocation=$env:OLY_DIR+'olympian.ico,0';$s.WindowStyle=7;$s.Description='Olympian NMS Save Editor';$s.Save()" >> "%LOG%" 2>&1
rem Comprobacion rapida visible; luego la app se abre sin ventana negra (pythonw)
".venv\Scripts\python.exe" -m olympian --check >> "%LOG%" 2>&1
if errorlevel 1 goto crashed
if not exist ".venv\Scripts\pythonw.exe" goto console
start "" ".venv\Scripts\pythonw.exe" -m olympian --log "%LOG%"
exit /b 0

:console
".venv\Scripts\python.exe" -m olympian --log "%LOG%"
exit /b 0

:venvfail
echo  No pude preparar el entorno de Olympian. Pasame el contenido de olympian_log.txt
pause
exit /b 1

:crashed
echo.
echo  Olympian se cerro con un error. Pasame el contenido de olympian_log.txt
pause
exit /b 1

rem --- prueba un Python: debe arrancar, ser 3.11+ y tener venv ---------------------
:try
%* -c "import sys, encodings, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set PY=%*
exit /b 0
