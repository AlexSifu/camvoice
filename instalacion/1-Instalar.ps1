# Instalación de camvoice: comprueba la máquina y registra la cámara virtual.
#
# Es idempotente: se puede ejecutar varias veces sin romper nada.
# No necesita permisos de administrador ni pide elevación: la cámara se registra
# solo para el usuario actual (HKCU) y sus DLL se copian a %LOCALAPPDATA%\camvoice.
#
#   .\1-Instalar.ps1
#   .\1-Instalar.ps1 -Nombre "Mi cámara"

[CmdletBinding()]
param(
    [string]$Nombre = 'CamVoice Virtual Camera'
)

. (Join-Path $PSScriptRoot '_comun.ps1')

Write-Titulo 'camvoice - Instalacion'

# --------------------------------------------------------------------------- #
# 1. Requisitos de la máquina
# --------------------------------------------------------------------------- #

Write-Paso 1 'Comprobando la maquina'
if (-not (Test-Requisitos)) {
    Write-Host ''
    Write-Error2 'La maquina no cumple los requisitos minimos. Ver docs/REQUISITOS.md'
    Pausar
    exit 1
}

# --------------------------------------------------------------------------- #
# 2. Localizar la aplicación
# --------------------------------------------------------------------------- #

Write-Paso 2 'Localizando la aplicacion'
$app = Find-Camvoice
if ($null -eq $app) {
    Write-Error2 'No se encontro camvoice.'
    Write-Info   'Este script debe estar junto a camvoice-cli.exe, o dentro del'
    Write-Info   'arbol de codigo fuente (carpeta instalacion\).'
    Pausar
    exit 1
}
Write-Ok "Encontrada via $($app.Tipo)"
Write-Info $app.Programa

# --------------------------------------------------------------------------- #
# 3. Estado previo
# --------------------------------------------------------------------------- #

Write-Paso 3 'Camaras ya registradas'
Write-Info "Usuario: $env:USERDOMAIN\$env:USERNAME (sin administrador)"
Write-AvisoElevado
$previas = @(Get-CamarasVirtuales)
if ($previas.Count -eq 0) {
    Write-Info 'Ninguna.'
} else {
    foreach ($c in $previas) { Write-Info (Format-Camara $c) }
    if (@($previas | Where-Object { $_.Nombre -eq $Nombre -and $_.Ambito -eq 'usuario' }).Count) {
        Write-Info "«$Nombre» ya estaba registrada para tu usuario. Se actualiza (no pasa nada)."
    }
}

# --------------------------------------------------------------------------- #
# 4. Registro solo para este usuario
# --------------------------------------------------------------------------- #

Write-Paso 4 "Registrando la camara como «$Nombre» (solo para este usuario)"
$res = Invoke-Camvoice $app @('install-camera', '--name', $Nombre)
Write-SalidaCamvoice $res.Salida
if ($res.Codigo -eq 2) {
    Write-Error2 'Ya hay un registro de este filtro del sistema o de otro programa.'
    Write-Info   'No se ha cambiado nada. camvoice no lo modifica ni lo oculta.'
    Write-Info   'Ver docs\VALIDACION_INSTALACION_USUARIO.md, apartado «Conflictos».'
    Pausar
    exit 2
}
if ($res.Codigo -ne 0) {
    Write-Error2 "El registro fallo (codigo $($res.Codigo)). Ver el detalle arriba."
    Write-Info   'Si algo se llego a escribir, se ha deshecho.'
    Pausar
    exit 1
}

# --------------------------------------------------------------------------- #
# 5. Verificación independiente
# --------------------------------------------------------------------------- #

Write-Paso 5 'Verificando en el registro de Windows (HKCU)'
# Se consulta el registro directamente, no la salida de la aplicacion: asi la
# comprobacion es independiente de lo que la aplicacion crea haber hecho.
$ahora = @(Get-CamarasVirtuales | Where-Object { $_.Nombre -eq $Nombre -and $_.Ambito -eq 'usuario' })
if ($ahora.Count -eq 0) {
    Write-Error2 "«$Nombre» no aparece en el registro de tu usuario."
    Pausar
    exit 1
}
foreach ($c in $ahora) { Write-Ok (Format-Camara $c) }

$arquitecturas = @($ahora | ForEach-Object { $_.Arq } | Sort-Object -Unique)
if ($arquitecturas.Count -lt 2) {
    Write-Aviso 'Solo se registro una arquitectura.'
    Write-Info  'Una aplicacion de videollamada de 32 bits no vera la camara:'
    Write-Info  'un filtro DirectShow solo se carga en procesos de su misma arquitectura.'
}

# --------------------------------------------------------------------------- #
# 6. Verificación de punta a punta
# --------------------------------------------------------------------------- #

Write-Paso 6 'Comprobando que DirectShow la enumera y entrega imagen'
$ver = Invoke-Camvoice $app @('verify-camera', '--name', $Nombre)
Write-SalidaCamvoice $ver.Salida
if ($ver.Codigo -ne 0) {
    Write-Error2 'La verificacion encontro fallos. La camara esta registrada pero'
    Write-Info   'algo no funciona; ver las lineas en rojo. Ejecuta .\3-Diagnostico.ps1'
    Pausar
    exit 1
}

# --------------------------------------------------------------------------- #
# 7. Diagnóstico completo
# --------------------------------------------------------------------------- #

Write-Paso 7 'Diagnostico de la aplicacion'
$diag = Invoke-Camvoice $app @('doctor')
Write-SalidaCamvoice $diag.Salida

# --------------------------------------------------------------------------- #
# 8. Resumen
# --------------------------------------------------------------------------- #

Write-Titulo 'Instalacion terminada'

if ($diag.Codigo -ne 0) {
    Write-Aviso 'El diagnostico reporto avisos. La camara esta registrada, pero'
    Write-Aviso 'falta configurar algo (normalmente, seleccionar los dos clips).'
    Write-Host ''
}

Write-Host '  Siguiente paso: prueba rapida sin grabar nada' -ForegroundColor White
Write-Host '      .\2-ProbarRapido.ps1' -ForegroundColor Cyan
Write-Host ''
Write-Host '  O abre la aplicacion directamente:' -ForegroundColor White
if ($app.Gui -and (Test-Path $app.Gui)) {
    Write-Host "      $($app.Gui)" -ForegroundColor Cyan
} else {
    Write-Host '      python -m camvoice' -ForegroundColor Cyan
}
Write-Host ''
Write-Host "  En Teams, Zoom o Chrome elige la camara: «$Nombre»" -ForegroundColor White
Write-Host '  Importante: cierralos y vuelve a abrirlos. Enumeran las camaras' -ForegroundColor DarkGray
Write-Host '  al arrancar y no se enteran de una nueva a mitad de sesion.' -ForegroundColor DarkGray

Pausar
exit 0
