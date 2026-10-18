# Diagnóstico. No cambia nada en el sistema: solo mira y reporta.
#
# Es lo que hay que ejecutar cuando algo no funciona, y lo que conviene pegar
# al reportar un problema.
#
#   .\3-Diagnostico.ps1
#   .\3-Diagnostico.ps1 -Guardar      # vuelca todo a un .txt en
#                                     # %LOCALAPPDATA%\camvoice\diagnosticos

[CmdletBinding()]
param(
    [switch]$Guardar
)

. (Join-Path $PSScriptRoot '_comun.ps1')

if ($Guardar) {
    # En el perfil del usuario: la carpeta del programa puede no ser escribible.
    $carpeta = Join-Path $env:LOCALAPPDATA 'camvoice\diagnosticos'
    New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
    $destino = Join-Path $carpeta ("diagnostico-{0:yyyyMMdd-HHmmss}.txt" -f (Get-Date))
    Start-Transcript -Path $destino | Out-Null
}

Write-Titulo 'camvoice - Diagnostico'

# --------------------------------------------------------------------------- #

Write-Paso 1 'Maquina'
Test-Requisitos | Out-Null
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Info "CPU: $($cpu.Name.Trim())"
Write-Info "Nucleos: $($cpu.NumberOfCores) fisicos / $($cpu.NumberOfLogicalProcessors) logicos"
Write-Info "PowerShell: $($PSVersionTable.PSVersion)"

# --------------------------------------------------------------------------- #

Write-Paso 2 'Usuario y permisos'
Write-Info "$env:USERDOMAIN\$env:USERNAME"
if (Test-ProcesoElevado) {
    Write-AvisoElevado
} else {
    Write-Ok 'Sesion normal: es la adecuada para instalar, usar y diagnosticar.'
}

# --------------------------------------------------------------------------- #

Write-Paso 3 'Camaras registradas (HKCU = tu usuario, HKLM = sistema; solo lectura)'
$camaras = @(Get-CamarasVirtuales)
if ($camaras.Count -eq 0) {
    Write-Info 'Ninguna entrada de dispositivo de video en el registro.'
    Write-Info 'Las webcams USB modernas pueden no aparecer aqui; ver el paso 6.'
} else {
    foreach ($c in $camaras) {
        $esVirtual = $c.Nombre -match 'CamVoice|Unity Video Capture|OBS Virtual'
        if ($esVirtual) { Write-Ok "$(Format-Camara $c)  <- virtual" }
        else            { Write-Info (Format-Camara $c) }
    }
}
$propias = @($camaras | Where-Object { $_.Ambito -eq 'usuario' -and $_.Nombre -match 'CamVoice|Unity Video Capture' })
if ($propias.Count -eq 0) {
    Write-Aviso 'No hay camara de camvoice registrada para tu usuario. Ejecuta  .\1-Instalar.ps1'
} else {
    $arq = @($propias | ForEach-Object { $_.Arq } | Sort-Object -Unique)
    if ($arq.Count -lt 2) {
        Write-Aviso "Solo registrada en $($arq -join ', '). Las apps de 32 bits no la veran."
    }
}

# --------------------------------------------------------------------------- #

Write-Paso 4 'Procesos que podrian ocupar la camara'
$conflictos = @(Get-Process -Name 'obs64', 'obs32', 'camvoice' -ErrorAction SilentlyContinue)
if ($conflictos.Count -eq 0) {
    Write-Ok 'Ninguno'
} else {
    foreach ($p in $conflictos) {
        Write-Aviso "$($p.ProcessName) (PID $($p.Id)) esta en ejecucion."
    }
    Write-Info 'Un filtro solo admite una aplicacion alimentandolo a la vez.'
}

# --------------------------------------------------------------------------- #

Write-Paso 5 'Aplicacion'
$app = Find-Camvoice
if ($null -eq $app) {
    Write-Error2 'No se encontro camvoice junto a estos scripts.'
    if ($Guardar) { Stop-Transcript | Out-Null }
    Pausar
    exit 1
}
Write-Ok "Encontrada via $($app.Tipo)"
Write-Info $app.Programa

# --------------------------------------------------------------------------- #

Write-Paso 6 'Diagnostico interno (doctor)'
$diag = Invoke-Camvoice $app @('doctor')
foreach ($linea in ($diag.Salida -split "`r?`n")) {
    if (-not $linea.Trim()) { continue }
    if     ($linea -match '\[--\]') { Write-Host "    $linea" -ForegroundColor Red }
    elseif ($linea -match '\[!!\]') { Write-Host "    $linea" -ForegroundColor Yellow }
    elseif ($linea -match '\[ok\]') { Write-Host "    $linea" -ForegroundColor Green }
    else                            { Write-Info $linea }
}

# --------------------------------------------------------------------------- #

Write-Paso 7 'Microfonos'
$mics = Invoke-Camvoice $app @('devices')
foreach ($linea in ($mics.Salida -split "`r?`n" | Select-Object -First 14)) {
    if ($linea.Trim()) { Write-Info $linea }
}

# --------------------------------------------------------------------------- #

Write-Paso 8 'Espacio que ocupa'
$datos = Join-Path $env:APPDATA 'camvoice'
if (Test-Path $datos) {
    $tam = (Get-ChildItem $datos -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    Write-Info ("Datos y cache: {0:N0} MB en {1}" -f ($tam / 1MB), $datos)
    Write-Info 'Se vacia con:  camvoice-cli clean'
} else {
    Write-Info 'Todavia no hay datos: la aplicacion no se ha ejecutado.'
}

# --------------------------------------------------------------------------- #

Write-Titulo 'Diagnostico terminado'

if ($diag.Codigo -eq 0) {
    Write-Host '  Todo correcto. Si aun asi no ves imagen en la videollamada:' -ForegroundColor Green
    Write-Host '    - Cierra y vuelve a abrir esa aplicacion (enumera las camaras al arrancar).' -ForegroundColor DarkGray
    Write-Host '    - Comprueba que no estas usando la app Camara de Windows (no vale).' -ForegroundColor DarkGray
} else {
    Write-Host '  Hay problemas. Revisa las lineas en rojo de arriba.' -ForegroundColor Yellow
}

if ($Guardar) {
    Stop-Transcript | Out-Null
    Write-Host ''
    Write-Host "  Informe guardado en: $destino" -ForegroundColor Cyan
}

Pausar
exit $diag.Codigo
