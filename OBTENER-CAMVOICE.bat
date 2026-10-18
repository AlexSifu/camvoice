@echo off
REM Descarga la version portable oficial de camvoice (GitHub Releases),
REM verifica su SHA-256, la descomprime en %LOCALAPPDATA%\Programs\camvoice
REM y lanza la instalacion de la camara solo para tu usuario.
REM
REM No necesita administrador ni Python. -ExecutionPolicy Bypass afecta solo a
REM esta invocacion; no cambia la configuracion del equipo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\obtener-portable.ps1" -Instalar %*
if errorlevel 1 pause
