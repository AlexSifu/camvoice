@echo off
REM Lanzador de doble clic para 9-Desinstalar.ps1
REM
REM Existe por una sola razon: por defecto Windows bloquea la ejecucion de
REM scripts .ps1 ("la ejecucion de scripts esta deshabilitada en este sistema").
REM -ExecutionPolicy Bypass lo sortea solo para esta invocacion, sin cambiar
REM la configuracion del equipo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp09-Desinstalar.ps1" %*
