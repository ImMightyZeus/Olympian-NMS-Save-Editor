@echo off
setlocal
rem Verifica las herramientas de datos del juego y mide cuanto tarda generarlos.
rem No modifica el juego ni los datos de Olympian. Usa el Python de Olympian (.venv).
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\gamedata_check.py %*
) else (
    echo  Abre Olympian una vez con run_olympian_nmsse.bat para que prepare su Python,
    echo  y despues vuelve a ejecutar este archivo.
)
echo.
pause
