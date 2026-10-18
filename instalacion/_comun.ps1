# Funciones compartidas por los scripts de instalación.
# No se ejecuta directamente: lo cargan los demás con dot-sourcing.

$ErrorActionPreference = 'Stop'

# La aplicación escribe en UTF-8. Sin esto, PowerShell decodifica su salida con
# la página de códigos ANSI del sistema y los acentos aparecen como «Micr?fono».
# Afecta solo a esta sesión; no cambia la configuración del equipo.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    # Consolas sin soporte (redirigidas, algunos hosts): se sigue sin ello.
}

# --------------------------------------------------------------------------- #
# Presentación
# --------------------------------------------------------------------------- #

function Write-Titulo($texto) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host "  $texto" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host ''
}

function Write-Paso($numero, $texto) {
    Write-Host ''
    Write-Host "[$numero] $texto" -ForegroundColor White
}

function Write-Ok($texto)     { Write-Host "    [ok] $texto" -ForegroundColor Green }
function Write-Aviso($texto)  { Write-Host "    [!!] $texto" -ForegroundColor Yellow }
function Write-Error2($texto) { Write-Host "    [--] $texto" -ForegroundColor Red }
function Write-Info($texto)   { Write-Host "         $texto" -ForegroundColor DarkGray }

function Pausar {
    # Solo tiene sentido si hay una consola interactiva: con doble clic en el
    # .bat la ventana se cierra sola al terminar y no daría tiempo a leer nada.
    if ($Host.Name -eq 'ConsoleHost' -and -not [Console]::IsInputRedirected) {
        Write-Host ''
        Write-Host 'Pulsa una tecla para cerrar...' -ForegroundColor DarkGray
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    }
}

# --------------------------------------------------------------------------- #
# Permisos: ninguno. Todo se instala en el perfil del usuario actual.
# --------------------------------------------------------------------------- #

function Test-ProcesoElevado {
    <#
      Indica si esta ventana se abrió con «Ejecutar como administrador».

      Solo se informa: la cámara se registra para el usuario actual (HKCU) y no
      necesita elevación. Al contrario, COM no lee el registro por usuario en
      procesos elevados, así que una comprobación hecha desde aquí no sería
      representativa de lo que ve Teams o el navegador.
    #>
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-AvisoElevado {
    if (Test-ProcesoElevado) {
        Write-Aviso 'Esta ventana se abrio como administrador. No hace falta.'
        Write-Info  'La camara se registra solo para tu usuario. Las aplicaciones'
        Write-Info  'elevadas no ven ese registro, asi que la verificacion puede fallar'
        Write-Info  'aqui aunque funcione en Teams o el navegador. Mejor: cierra esta'
        Write-Info  'ventana y haz doble clic en el .bat sin «Ejecutar como administrador».'
    }
}

# --------------------------------------------------------------------------- #
# Localización de la aplicación
# --------------------------------------------------------------------------- #

function Find-Camvoice {
    <#
      Devuelve un objeto con cómo invocar la aplicación.

      Funciona en los dos escenarios sin que el usuario tenga que elegir:
        - paquete distribuido: camvoice-cli.exe junto a los scripts o un nivel
          por encima;
        - árbol de código fuente: se usa el entorno virtual .venv, y si no, el
          Python del sistema.
    #>
    $raiz = Split-Path -Parent $PSScriptRoot

    $candidatosExe = @(
        (Join-Path $PSScriptRoot 'camvoice-cli.exe'),
        (Join-Path $raiz 'camvoice-cli.exe'),
        (Join-Path $raiz 'dist\camvoice\camvoice-cli.exe')
    )
    foreach ($exe in $candidatosExe) {
        if (Test-Path $exe) {
            return [pscustomobject]@{
                Tipo     = 'ejecutable'
                Programa = $exe
                Base     = @()
                Gui      = (Join-Path (Split-Path -Parent $exe) 'camvoice.exe')
                Raiz     = Split-Path -Parent $exe
            }
        }
    }

    $candidatosPython = @(
        (Join-Path $raiz '.venv\Scripts\python.exe'),
        'python.exe'
    )
    foreach ($py in $candidatosPython) {
        $existe = (Test-Path $py) -or ($null -ne (Get-Command $py -ErrorAction SilentlyContinue))
        if ($existe -and (Test-Path (Join-Path $raiz 'camvoice\__main__.py'))) {
            return [pscustomobject]@{
                Tipo     = 'codigo fuente'
                Programa = $py
                Base     = @('-m', 'camvoice')
                Gui      = $null
                Raiz     = $raiz
            }
        }
    }
    return $null
}

function Invoke-Camvoice($app, [string[]]$argumentos) {
    <# Ejecuta un subcomando y devuelve la salida junto al código de salida. #>
    Push-Location $app.Raiz
    try {
        $todos = @($app.Base) + $argumentos
        $salida = & $app.Programa @todos 2>&1 | Out-String
        return [pscustomobject]@{ Salida = $salida; Codigo = $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

# --------------------------------------------------------------------------- #
# Comprobaciones del sistema
# --------------------------------------------------------------------------- #

function Test-Requisitos {
    <# Devuelve $true si la máquina puede ejecutar la aplicación. #>
    $ok = $true

    $os = Get-CimInstance Win32_OperatingSystem
    $version = [Version]$os.Version
    if ($version.Major -ge 10) {
        Write-Ok "Windows $($os.Caption.Trim()) (build $($version.Build))"
    } else {
        Write-Error2 "Hace falta Windows 10 u 11. Detectado: $($os.Caption)"
        $ok = $false
    }

    if ([Environment]::Is64BitOperatingSystem) {
        Write-Ok 'Windows de 64 bits'
    } else {
        Write-Error2 'Hace falta un Windows de 64 bits.'
        $ok = $false
    }

    $ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    if ($ram -ge 7.5) { Write-Ok "$ram GB de RAM" }
    else { Write-Aviso "$ram GB de RAM. Recomendado 8 GB o más." }

    $unidad = (Get-Item $PSScriptRoot).PSDrive.Name
    $libre = [math]::Round((Get-PSDrive $unidad).Free / 1GB, 1)
    if ($libre -ge 1.5) {
        Write-Ok "$libre GB libres en $unidad`:"
    } else {
        Write-Aviso "Solo $libre GB libres en $unidad`:. La caché de fotogramas necesita espacio."
        Write-Info  'Unos 7 MB por segundo de clip a 640x480 y 15 FPS.'
    }

    $micros = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
                Where-Object { $_.PNPClass -in @('AudioEndpoint', 'Media') -and $_.Status -eq 'OK' })
    if ($micros.Count -gt 0) {
        Write-Ok "$($micros.Count) dispositivos de audio en el sistema"
    } else {
        Write-Aviso 'No se detectaron dispositivos de audio por WMI.'
        Write-Info  'El diagnóstico de la aplicación lo confirmará más adelante.'
    }

    return $ok
}

function Get-CamarasVirtuales {
    <#
      Filtros DirectShow registrados: por usuario (HKCU) y del sistema (HKLM),
      en las vistas de 64 y 32 bits. Solo lee.

      Las vistas se abren con RegistryView, que es lo que hace Windows; no se
      compone WOW6432Node a mano. Ambito = 'usuario' o 'sistema'.
    #>
    $clave = 'Software\Classes\CLSID\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\Instance'
    $colmenas = @(
        @{ Hive = [Microsoft.Win32.RegistryHive]::CurrentUser;  Ambito = 'usuario' },
        @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine; Ambito = 'sistema' }
    )
    $vistas = @(
        @{ Vista = [Microsoft.Win32.RegistryView]::Registry64; Arq = '64-bit' },
        @{ Vista = [Microsoft.Win32.RegistryView]::Registry32; Arq = '32-bit' }
    )
    $resultado = @()
    foreach ($c in $colmenas) {
        foreach ($v in $vistas) {
            $base = $null
            $raiz = $null
            try {
                $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($c.Hive, $v.Vista)
                $raiz = $base.OpenSubKey($clave)
                if ($null -eq $raiz) { continue }
                foreach ($nombre in $raiz.GetSubKeyNames()) {
                    $sub = $raiz.OpenSubKey($nombre)
                    if ($null -eq $sub) { continue }
                    try {
                        $amigable = $sub.GetValue('FriendlyName')
                        if ($amigable) {
                            $resultado += [pscustomobject]@{
                                Nombre = [string]$amigable
                                Arq    = $v.Arq
                                Ambito = $c.Ambito
                                Clsid  = [string]$sub.GetValue('CLSID')
                            }
                        }
                    } finally { $sub.Close() }
                }
            } catch {
                Write-Aviso "No se pudo leer $($c.Ambito) $($v.Arq): $($_.Exception.Message)"
            } finally {
                if ($raiz) { $raiz.Close() }
                if ($base) { $base.Close() }
            }
        }
    }
    return $resultado
}

function Format-Camara($c) {
    $ambito = if ($c.Ambito -eq 'usuario') { 'este usuario' } else { 'sistema, solo lectura' }
    return "$($c.Nombre)  ($($c.Arq), $ambito)"
}

function Write-SalidaCamvoice($salida) {
    <# Reimprime la salida de camvoice-cli con colores según su marca. #>
    foreach ($linea in ($salida -split "`r?`n")) {
        if (-not $linea.Trim()) { continue }
        if     ($linea -match '\[--\]') { Write-Host "    $linea" -ForegroundColor Red }
        elseif ($linea -match '\[!!\]') { Write-Host "    $linea" -ForegroundColor Yellow }
        elseif ($linea -match '\[ok\]') { Write-Host "    $linea" -ForegroundColor Green }
        else                            { Write-Info $linea }
    }
}
