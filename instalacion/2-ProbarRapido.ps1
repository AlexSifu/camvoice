# Prueba rápida de extremo a extremo, sin grabar ningún video.
#
# Genera dos clips sintéticos, prepara la caché y emite a la cámara virtual
# durante unos segundos, para comprobar la cadena completa antes de molestarse
# en grabar nada.
#
#   .\2-ProbarRapido.ps1
#   .\2-ProbarRapido.ps1 -Segundos 60
#   .\2-ProbarRapido.ps1 -SoloVad        # solo el detector de voz
#
# No necesita permisos de administrador.

[CmdletBinding()]
param(
    [int]$Segundos = 25,
    [switch]$SoloVad
)

. (Join-Path $PSScriptRoot '_comun.ps1')

Write-Titulo 'camvoice - Prueba rapida'

# --------------------------------------------------------------------------- #

Write-Paso 1 'Localizando la aplicacion'
$app = Find-Camvoice
if ($null -eq $app) {
    Write-Error2 'No se encontro camvoice. Ejecuta antes .\1-Instalar.ps1'
    Pausar
    exit 1
}
Write-Ok "Encontrada via $($app.Tipo)"

# --------------------------------------------------------------------------- #

Write-Paso 2 'Comprobando que la camara esta registrada'
$camaras = Get-CamarasVirtuales
$propias = @($camaras | Where-Object { $_.Ambito -eq 'usuario' -and $_.Nombre -match 'CamVoice|Unity Video Capture' })
if ($propias.Count -eq 0) {
    Write-Error2 'No hay ninguna camara virtual de camvoice registrada para tu usuario.'
    Write-Info   'Ejecuta primero:  .\1-Instalar.ps1'
    Pausar
    exit 1
}
foreach ($c in $propias) { Write-Ok (Format-Camara $c) }

# --------------------------------------------------------------------------- #

if ($SoloVad) {
    Write-Paso 3 "Probando el detector de voz durante $Segundos s"
    Write-Host ''
    Write-Host '  GUION:' -ForegroundColor White
    Write-Host '    1. Quedate en silencio 10 s  -> no debe haber ningun cambio de estado' -ForegroundColor DarkGray
    Write-Host '    2. Di "uno, dos, tres"       -> debe pasar a SPEAKING en ~120 ms' -ForegroundColor DarkGray
    Write-Host '    3. Calla                     -> vuelve a SILENT en ~450 ms' -ForegroundColor DarkGray
    Write-Host '    4. Teclea o mueve papeles    -> no debe cambiar de estado' -ForegroundColor DarkGray
    Write-Host ''
    Push-Location $app.Raiz
    try {
        $todos = @($app.Base) + @('vad-test', '--duration', "$Segundos")
        & $app.Programa @todos
    } finally { Pop-Location }
    Pausar
    exit 0
}

# --------------------------------------------------------------------------- #

Write-Paso 3 'Generando dos clips sinteticos de prueba'
$clips = Join-Path $app.Raiz 'clips'
$res = Invoke-Camvoice $app @('make-clips', '--outdir', $clips, '--seconds', '8')
if ($res.Codigo -ne 0) {
    Write-Error2 'No se pudieron generar los clips.'
    Write-Info   $res.Salida
    Pausar
    exit 1
}
$hablando = Join-Path $clips 'habla-prueba.mp4'
$silencio = Join-Path $clips 'silencio-prueba.mp4'
Write-Ok 'habla-prueba.mp4 y silencio-prueba.mp4'
Write-Info $clips

# --------------------------------------------------------------------------- #

Write-Paso 4 'Abriendo la pagina de prueba en el navegador'
Write-Info 'Elige alli la camara «CamVoice Virtual Camera».'
Write-Info 'NO uses la app Camara de Windows: no soporta DirectShow.'
try {
    Start-Process 'https://webcamtests.com/' | Out-Null
    Write-Ok 'webcamtests.com abierto'
} catch {
    Write-Aviso 'No se pudo abrir el navegador. Abre https://webcamtests.com/ a mano.'
}

Write-Host ''
Write-Host '  Tienes 8 segundos para elegir la camara en el navegador...' -ForegroundColor Yellow
Start-Sleep -Seconds 8

# --------------------------------------------------------------------------- #

Write-Paso 5 "Emitiendo a la camara virtual durante $Segundos s"
Write-Host ''
Write-Host '  QUE DEBES VER en el navegador:' -ForegroundColor White
Write-Host '    - Una cara esquematica sobre fondo degradado.' -ForegroundColor DarkGray
Write-Host '    - Al HABLAR: la boca se mueve y pone "HABLANDO".' -ForegroundColor DarkGray
Write-Host '    - Al CALLAR: boca cerrada y pone "SILENCIO".' -ForegroundColor DarkGray
Write-Host '    - El cambio es un fundido suave, sin negros ni parpadeos.' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Habla y calla varias veces para ver las transiciones.' -ForegroundColor Yellow
Write-Host ''

Push-Location $app.Raiz
try {
    $todos = @($app.Base) + @(
        'run', '--speaking', $hablando, '--silent', $silencio,
        '--duration', "$Segundos"
    )
    & $app.Programa @todos
    $codigo = $LASTEXITCODE
} finally { Pop-Location }

# --------------------------------------------------------------------------- #

Write-Titulo 'Prueba terminada'

if ($codigo -ne 0) {
    Write-Error2 "La ejecucion termino con codigo $codigo."
    Write-Info   'Ejecuta  .\3-Diagnostico.ps1  para ver que falla.'
    Pausar
    exit $codigo
}

Write-Host '  Si viste la imagen moverse con tu voz, la cadena completa funciona.' -ForegroundColor Green
Write-Host ''
Write-Host '  Siguientes pasos:' -ForegroundColor White
Write-Host '    - Graba tus dos clips reales (ver docs\REQUISITOS.md seccion 2.2).' -ForegroundColor DarkGray
Write-Host '    - Abre camvoice.exe, seleccionalos y pulsa Iniciar.' -ForegroundColor DarkGray
Write-Host '    - Para ajustar la sensibilidad:  .\2-ProbarRapido.ps1 -SoloVad' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Si NO viste imagen:' -ForegroundColor White
Write-Host '    - Cierra y vuelve a abrir el navegador: enumera las camaras al arrancar.' -ForegroundColor DarkGray
Write-Host '    - Ejecuta  .\3-Diagnostico.ps1' -ForegroundColor DarkGray

Pausar
exit 0
