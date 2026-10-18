# Compila la version portable de camvoice y forma el ZIP distribuible.
#
#   .\scripts\build-portable.ps1                 # pruebas + compilacion + ZIP
#   .\scripts\build-portable.ps1 -SinPruebas     # solo compilacion + ZIP
#   .\scripts\build-portable.ps1 -Python C:\ruta\python.exe
#
# No necesita administrador ni instala nada fuera del proyecto. Deja:
#
#   dist\camvoice\                 la carpeta portable
#   dist\camvoice-win64.zip        el ZIP (contenido en la raiz: instalacion\INSTALAR.bat, ...)
#   dist\camvoice-win64.zip.sha256 "<sha256>  camvoice-win64.zip"
#
# Sale con 0 si todo fue bien y con 1 al primer fallo.

[CmdletBinding()]
param(
    [string]$Python,
    [switch]$SinPruebas
)

# 'Continue': en Windows PowerShell 5.1 con 'Stop', cualquier linea que pytest o
# PyInstaller escriban en stderr aborta el script si la salida se redirige.
# Cada paso comprueba $LASTEXITCODE explicitamente.
$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Fallo([string]$mensaje) {
    Write-Host "[--] $mensaje" -ForegroundColor Red
    exit 1
}
function Ok([string]$mensaje) { Write-Host "[ok] $mensaje" -ForegroundColor Green }

if (-not $Python) {
    $venv = Join-Path $raiz '.venv\Scripts\python.exe'
    $Python = if (Test-Path $venv) { $venv } else { 'python' }
}
& $Python --version
if ($LASTEXITCODE -ne 0) { Fallo "No se encuentra Python ($Python). Ver docs\INSTALACION.md seccion 3." }

$version = (Select-String -Path (Join-Path $raiz 'camvoice\__init__.py') -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Ok "camvoice $version"

# 1. Pruebas -------------------------------------------------------------------
if (-not $SinPruebas) {
    & $Python -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Fallo 'Las pruebas fallan; no se empaqueta.' }
    Ok 'Pruebas superadas'
}

# 2. Compilacion ---------------------------------------------------------------
& $Python -m PyInstaller build\camvoice.spec --noconfirm --distpath dist --workpath build\camvoice
if ($LASTEXITCODE -ne 0) { Fallo 'PyInstaller fallo.' }

$app = Join-Path $raiz 'dist\camvoice'
$obligatorios = @(
    'camvoice.exe',
    'camvoice-cli.exe',
    '_internal\filters\UnityCaptureFilter64.dll',
    '_internal\filters\UnityCaptureFilter32.dll',
    '_internal\filters\ATRIBUCION.md',
    '_internal\PySide6',
    '_internal\cv2',
    '_internal\numpy',
    '_internal\pyvirtualcam',
    'instalacion\INSTALAR.bat',
    'instalacion\1-Instalar.ps1',
    'instalacion\9-Desinstalar.ps1',
    'instalacion\_comun.ps1',
    'docs\README.md',
    'docs\LICENCIAS.md',
    'docs\ATRIBUCION.md'
)
$faltan = $obligatorios | Where-Object { -not (Test-Path (Join-Path $app $_)) }
if ($faltan) { Fallo ("Faltan en dist\camvoice: " + ($faltan -join ', ')) }
Ok 'Contenido del paquete completo'

# 3. Arranque del ejecutable (solo lectura: no registra nada) ------------------
# 'cameras' sale con 1 si el equipo no tiene ninguna camara (un runner de CI);
# eso es un resultado valido. Un fallo de arranque del ejecutable da otro codigo.
& (Join-Path $app 'camvoice-cli.exe') cameras | Out-Host
if ($LASTEXITCODE -notin 0, 1) { Fallo "camvoice-cli.exe cameras no arranca (codigo $LASTEXITCODE)." }
Ok 'camvoice-cli.exe arranca'

# 4. ZIP y SHA-256 -------------------------------------------------------------
# Sin programas externos a partir de aqui: cualquier error debe parar el script.
$ErrorActionPreference = 'Stop'
$zip = Join-Path $raiz 'dist\camvoice-win64.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
# Entrada a entrada en vez de ZipFile.CreateFromDirectory: en Windows PowerShell
# 5.1 (.NET Framework) este guarda las rutas con '\', fuera del formato ZIP.
$archivo = [IO.Compression.ZipFile]::Open($zip, [IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -Path $app -Recurse -File | ForEach-Object {
        $relativa = $_.FullName.Substring($app.Length + 1).Replace('\', '/')
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archivo, $_.FullName, $relativa, [IO.Compression.CompressionLevel]::Optimal)
    }
} finally {
    $archivo.Dispose()
}

$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLowerInvariant()
$lineaHash = "$hash  camvoice-win64.zip"
[IO.File]::WriteAllText("$zip.sha256", "$lineaHash`n", (New-Object Text.UTF8Encoding($false)))

$mb = '{0:N1}' -f ((Get-Item $zip).Length / 1MB)
Ok "ZIP: $zip ($mb MB)"
Ok "SHA-256: $hash"
exit 0
