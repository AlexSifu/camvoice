# Deja la máquina como estaba: quita el registro de la cámara y, si se pide,
# los datos de la aplicación.
#
#   .\9-Desinstalar.ps1
#   .\9-Desinstalar.ps1 -BorrarDatos     # tambien configuracion y cache
#
# No necesita administrador: solo quita el registro por usuario (HKCU) de
# camvoice y sus DLL de %LOCALAPPDATA%\camvoice\filters. Un registro del
# sistema (HKLM) o de otro programa no se toca; solo se informa.
#
# Conviene ejecutarlo antes de borrar la carpeta del programa: es quien sabe
# limpiar el registro. Las DLL registradas no dependen de esa carpeta.

[CmdletBinding()]
param(
    [switch]$BorrarDatos
)

. (Join-Path $PSScriptRoot '_comun.ps1')

Write-Titulo 'camvoice - Desinstalacion'

# --------------------------------------------------------------------------- #

Write-Paso 1 'Localizando la aplicacion'
$app = Find-Camvoice
if ($null -eq $app) {
    Write-Error2 'No se encontro camvoice junto a estos scripts.'
    Write-Info   'Si ya borraste la carpeta del programa, el registro de la camara'
    Write-Info   'sigue puesto. Vuelve a descomprimir y ejecuta este script.'
    Pausar
    exit 1
}
Write-Ok "Encontrada via $($app.Tipo)"

# --------------------------------------------------------------------------- #

Write-Paso 2 'Usuario'
Write-Info "$env:USERDOMAIN\$env:USERNAME (sin administrador)"
Write-AvisoElevado

# --------------------------------------------------------------------------- #

Write-Paso 3 'Cerrando la aplicacion si esta abierta'
$abiertos = @(Get-Process -Name 'camvoice', 'camvoice-cli' -ErrorAction SilentlyContinue)
if ($abiertos.Count -eq 0) {
    Write-Ok 'No estaba abierta'
} else {
    foreach ($p in $abiertos) {
        try {
            Stop-Process -Id $p.Id -Force
            Write-Ok "Cerrado $($p.ProcessName) (PID $($p.Id))"
        } catch {
            Write-Aviso "No se pudo cerrar $($p.ProcessName) (PID $($p.Id))"
        }
    }
    Start-Sleep -Seconds 1
}

# --------------------------------------------------------------------------- #

Write-Paso 4 'Quitando el registro de la camara de este usuario'
$antes = @(Get-CamarasVirtuales | Where-Object { $_.Ambito -eq 'usuario' -and $_.Nombre -match 'CamVoice|Unity Video Capture' })
foreach ($c in $antes) { Write-Info "Registrada ahora: $(Format-Camara $c)" }
# Se llama siempre: también limpia copias de DLL que quedaron en uso en una
# desinstalación anterior. Es idempotente.
$res = Invoke-Camvoice $app @('install-camera', '--uninstall')
Write-SalidaCamvoice $res.Salida
if ($res.Codigo -ne 0) {
    Write-Error2 "La desinstalacion fallo (codigo $($res.Codigo)). Ver el detalle arriba."
    Pausar
    exit 1
}

# --------------------------------------------------------------------------- #

Write-Paso 5 'Verificando'
# Se consulta el registro directamente, no lo que la aplicacion diga.
$despues = @(Get-CamarasVirtuales | Where-Object { $_.Nombre -match 'CamVoice|Unity Video Capture' })
$propias = @($despues | Where-Object { $_.Ambito -eq 'usuario' })
$ajenas = @($despues | Where-Object { $_.Ambito -ne 'usuario' })
if ($propias.Count -eq 0) {
    Write-Ok 'Tu usuario ya no tiene la camara de camvoice registrada'
} else {
    Write-Error2 'Siguen registradas para tu usuario (de otro programa, no se tocan):'
    foreach ($c in $propias) { Write-Info (Format-Camara $c) }
}
foreach ($c in $ajenas) {
    Write-Aviso "Registro del sistema, no se toca: $(Format-Camara $c)"
}

# --------------------------------------------------------------------------- #

Write-Paso 6 'Datos de la aplicacion'
$datos = Join-Path $env:APPDATA 'camvoice'
if (-not (Test-Path $datos)) {
    Write-Info 'No hay datos que borrar.'
} elseif ($BorrarDatos) {
    $tam = (Get-ChildItem $datos -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    try {
        Remove-Item $datos -Recurse -Force
        Write-Ok ("Borrados {0:N0} MB de {1}" -f ($tam / 1MB), $datos)
    } catch {
        Write-Error2 "No se pudo borrar $datos : $_"
    }
} else {
    $tam = (Get-ChildItem $datos -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    Write-Info ("Se conservan {0:N0} MB en {1}" -f ($tam / 1MB), $datos)
    Write-Info 'Incluye la configuracion y la cache de fotogramas.'
    Write-Info 'Para borrarlos tambien:  .\9-Desinstalar.ps1 -BorrarDatos'
}

# --------------------------------------------------------------------------- #

Write-Titulo 'Desinstalacion terminada'
Write-Host '  Ya puedes borrar la carpeta del programa.' -ForegroundColor White
if (-not $BorrarDatos -and (Test-Path $datos)) {
    Write-Host "  Quedan los datos en: $datos" -ForegroundColor DarkGray
}

Pausar
exit 0
