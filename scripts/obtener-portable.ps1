# Descarga la version portable oficial de camvoice desde GitHub Releases,
# comprueba su SHA-256 y la descomprime en el perfil del usuario.
#
#   .\scripts\obtener-portable.ps1                    # ultima version
#   .\scripts\obtener-portable.ps1 -Version v0.1.0    # una version concreta
#   .\scripts\obtener-portable.ps1 -Instalar          # y ejecuta INSTALAR.bat
#
# No necesita administrador, ni Python, ni Git LFS. Destino por defecto:
#   %LOCALAPPDATA%\Programs\camvoice\<version>\
#
# Si todavia no hay ninguna version publicada, lo dice y explica como
# compilarla desde el codigo (scripts\build-portable.ps1).

[CmdletBinding()]
param(
    [string]$Version = 'latest',
    [string]$Destino = (Join-Path $env:LOCALAPPDATA 'Programs\camvoice'),
    [string]$Repositorio = 'AlexSifu/camvoice',
    [switch]$Instalar
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest es 10 veces mas lento con la barra
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ZIP = 'camvoice-win64.zip'

function Fallo([string]$mensaje) {
    Write-Host ''
    Write-Host "[--] $mensaje" -ForegroundColor Red
    exit 1
}
function Ok([string]$mensaje) { Write-Host "[ok] $mensaje" -ForegroundColor Green }
function Info([string]$mensaje) { Write-Host "     $mensaje" }

$sinVersion = @"
No hay ninguna version portable publicada en https://github.com/$Repositorio/releases
(o no existe '$Version').

Alternativa: compilarla desde este clon, con Python 3.12 instalado para tu usuario:
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements-lock.txt
    .\scripts\build-portable.ps1
y usar dist\camvoice\instalacion\INSTALAR.bat. Ver docs\VALIDACION_PC_NUEVA.md.
"@

# 1. Localizar la version ------------------------------------------------------
$api = if ($Version -eq 'latest') {
    "https://api.github.com/repos/$Repositorio/releases/latest"
} else {
    "https://api.github.com/repos/$Repositorio/releases/tags/$Version"
}
try {
    $release = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'camvoice-obtener-portable' }
} catch {
    $codigo = $null
    if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
    if ($codigo -eq 404) { Fallo $sinVersion }
    Fallo "No se pudo consultar GitHub ($api): $($_.Exception.Message)"
}

$assetZip = $release.assets | Where-Object { $_.name -eq $ZIP } | Select-Object -First 1
$assetHash = $release.assets | Where-Object { $_.name -eq "$ZIP.sha256" } | Select-Object -First 1
if (-not $assetZip -or -not $assetHash) {
    Fallo "La version $($release.tag_name) no tiene $ZIP y $ZIP.sha256.`n$sinVersion"
}
$tag = $release.tag_name
Ok "Version $tag ($('{0:N1}' -f ($assetZip.size / 1MB)) MB)"

# 2. Descargar -----------------------------------------------------------------
$temporal = Join-Path $env:TEMP "camvoice-descarga-$tag"
New-Item -ItemType Directory -Force -Path $temporal | Out-Null
$rutaZip = Join-Path $temporal $ZIP
$rutaHash = "$rutaZip.sha256"

Info "Descargando $($assetZip.browser_download_url)"
Invoke-WebRequest -Uri $assetZip.browser_download_url -OutFile $rutaZip -UseBasicParsing
Invoke-WebRequest -Uri $assetHash.browser_download_url -OutFile $rutaHash -UseBasicParsing

# 3. Verificar integridad ------------------------------------------------------
$esperado = ((Get-Content $rutaHash -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$real = (Get-FileHash -Algorithm SHA256 $rutaZip).Hash.ToLowerInvariant()
if ($esperado -notmatch '^[0-9a-f]{64}$') { Fallo "El archivo $ZIP.sha256 no contiene un SHA-256 valido." }
if ($real -ne $esperado) {
    Remove-Item $rutaZip -Force
    Fallo "El SHA-256 no coincide; descarga borrada.`n     esperado $esperado`n     obtenido $real"
}
Ok "SHA-256 verificado: $real"

# 4. Descomprimir en el perfil del usuario -------------------------------------
$carpeta = Join-Path $Destino $tag
if (Test-Path (Join-Path $carpeta 'camvoice-cli.exe')) {
    Ok "Ya estaba descomprimida en $carpeta"
} else {
    New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($rutaZip, $carpeta)
    Get-ChildItem -Path $carpeta -Recurse -File | Unblock-File
    Ok "Descomprimida en $carpeta"
}
Remove-Item $temporal -Recurse -Force

$instalar = Join-Path $carpeta 'instalacion\INSTALAR.bat'
if (-not (Test-Path $instalar)) { Fallo "El paquete no contiene instalacion\INSTALAR.bat." }

Write-Host ''
if ($Instalar) {
    & $instalar
    exit $LASTEXITCODE
}
Write-Host 'Siguiente paso (sin administrador):'
Write-Host "    `"$instalar`""
Write-Host 'Despues abre:'
Write-Host "    `"$(Join-Path $carpeta 'camvoice.exe')`""
exit 0
