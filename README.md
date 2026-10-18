# camvoice

Cámara virtual para Windows que reproduce un clip pregrabado tuyo **hablando**
cuando detecta tu voz, y otro **en silencio** cuando callas.

Sin IA generativa, sin servicios externos, sin GPU y sin conexión a Internet.
Todo se procesa en local: el audio se analiza trama a trama para decidir entre
dos estados y se descarta; no se graba ni se envía a ninguna parte.

```
   micrófono ──► VAD ──► histéresis ──► ¿hablas? ──► clip correspondiente ──► cámara virtual
```

## Empezar

### Solo quiero probarlo

Descarga **[camvoice-win64.zip](https://github.com/AlexSifu/camvoice/releases/latest)**
(~95 MB, Windows x64, autocontenido) y su `.sha256`, descomprime y en `instalacion\`:

```
INSTALAR.bat     -> registra la cámara solo para tu usuario, sin administrador
PROBAR.bat       -> abre el navegador y verás la imagen moverse con tu voz
```

No necesita OBS, ni Python, ni drivers, ni Internet. Requisitos completos en
[docs/REQUISITOS.md](docs/REQUISITOS.md).

Desde un clon del repositorio, **`OBTENER-CAMVOICE.bat`** hace lo mismo solo:
descarga la última versión publicada, comprueba el SHA-256, la descomprime en
`%LOCALAPPDATA%\Programs\camvoice\<versión>\` y lanza `INSTALAR.bat`. Si aún no
hay ninguna versión publicada lo dice y remite a compilarla
([§ Generar el ejecutable](#generar-el-ejecutable)). Procedimiento completo para
una PC nueva: [docs/VALIDACION_PC_NUEVA.md](docs/VALIDACION_PC_NUEVA.md).

### Desde el código

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python -m camvoice doctor                  # comprueba el entorno
python -m camvoice make-clips --outdir clips   # clips de prueba, sin grabar nada
python -m camvoice                         # interfaz gráfica
```

Para usarlo en una videollamada, registra la cámara **una sola vez**:

```bash
python -m camvoice install-camera      # solo para tu usuario, sin administrador
python -m camvoice verify-camera       # comprueba registro, enumeración y fotogramas
```

No pide permisos de administrador: la cámara se registra en el perfil del
usuario actual (`HKCU` y `%LOCALAPPDATA%\camvoice`) y no se toca nada del
sistema.

En una máquina nueva es más rápido con los scripts de `instalacion\`: doble
clic en `INSTALAR.bat` y luego en `PROBAR.bat`.

Aparece como **«CamVoice Virtual Camera»** para las aplicaciones DirectShow.
**No hace falta OBS.** Detalles en [docs/CAMARA-VIRTUAL.md](docs/CAMARA-VIRTUAL.md);
qué aplicaciones se han probado, en
[docs/VALIDACION_INSTALACION_USUARIO.md](docs/VALIDACION_INSTALACION_USUARIO.md).

## Grabar los clips desde la propia aplicación

Pestaña **Grabar**: elige la cámara, la duración y si es el clip hablando o el
de silencio. Muestra guías de encuadre, cuenta atrás, y las señales del guion en
pantalla mientras graba. **Para sola** al cumplirse el tiempo.

```
 0.0 - 0.6 s   BOCA CERRADA, mira al objetivo
 0.6 - 3.8 s   "Esta mañana el tiempo parece bastante bueno,"
 3.8 - 6.3 s   "aunque por la tarde puede cambiar."
 6.3 - 9.4 s   "Me faltan algunos papeles para el informe,"
 9.4 - 13.5 s  "pero podemos verlo con calma y confirmar la fecha final."
13.5 - 15.0 s  Termina la frase y CIERRA LA BOCA
```

El texto sale **una frase cada vez**, con el tiempo repartido según su longitud,
y debajo se ve atenuada la siguiente para poder anticiparla. La cuenta atrás es
configurable de 0 a 15 s.

El medio segundo inicial no es decorativo: es el fotograma al que se rebobina el
clip de habla tras cada silencio. Si ahí tienes la boca a medio abrir, cada vez
que empieces a hablar la cara aparecerá con una mueca.

El guion completo —qué decir, qué no hacer, por qué el clip de silencio se
reproduce la mitad del tiempo hacia atrás— está en
[docs/GUION-GRABACION.md](docs/GUION-GRABACION.md).

## Segundo plano

Casilla en la pestaña **Salida**: al cerrar la ventana, la aplicación sigue en
el área de notificación con un icono que **cambia de color según el estado**
del detector. Doble clic para volver, «Salir» en su menú para terminar.

Quita la ventana de la barra de tareas y de Alt+Tab. **No oculta la aplicación
del sistema**: sigue en el Administrador de tareas, en el propio icono junto al
reloj, y en la lista de cámaras del programa de videollamada, donde aparece
«CamVoice Virtual Camera».

## Efectos

Sobre el fotograma de salida, ajustables en vivo desde la pestaña **Efectos** o
por línea de comandos. Todos son porcentajes derivados del ancho, así que el
60 % se ve igual a 320x240 que a 720p.

| Efecto | Para qué |
|---|---|
| **Desenfoque** | Privacidad. Hace la imagen no identificable sin ocultarla |
| **Pixelado** | Igual, con otro aspecto. Bloques de hasta 1/16 del ancho |
| **Brillo**, **contraste**, **saturación** | Igualar el clip con la luz actual de la sala |
| **Zoom** | Reencuadrar o quitar fondo sin volver a grabar |
| **Grano** | Un clip comprimido se ve demasiado limpio para ser una webcam |
| **Viñeta** | Oscurecer las esquinas |
| **Espejo** | En vivo, sin volver a preparar la caché |

Hay preajustes («Discreto», «Cámara mala», «Privacidad alta»…) porque nueve
deslizadores a cero no sugieren qué hacer con ellos.

```bash
camvoice run --blur 45 --grain 20 --vignette 30 --saturation -25
camvoice bench --duration 60 --metrics --pixelate 60   # medir su coste
```

Coste medido de la cadena completa a 640x480: **5,6 ms por fotograma** de los
66,7 disponibles, y **13,8 % de un núcleo**. Llegar ahí requirió tres
optimizaciones; están en [docs/METRICS.md](docs/METRICS.md) §Medición 3.

## Comandos

| Comando | Para qué |
|---|---|
| `camvoice` | Interfaz gráfica (acción por defecto) |
| `camvoice doctor` | Comprueba entorno, audio, cámara virtual y configuración |
| `camvoice devices` | Lista los micrófonos |
| `camvoice cameras` | Lista los filtros de cámara registrados en Windows |
| `camvoice install-camera` | Registra la cámara «CamVoice Virtual Camera» para el usuario actual (sin OBS ni administrador); `--uninstall` la quita |
| `camvoice verify-camera` | Comprueba registro, DLL, enumeración en 64/32 bits y fotogramas reales |
| `camvoice probe video.mp4` | Propiedades de un clip |
| `camvoice prepare` | Construye la caché de fotogramas |
| `camvoice run` | Ejecuta sin interfaz |
| `camvoice vad-test` | Ajusta la sensibilidad del detector con el micrófono real |
| `camvoice bench --duration 1800` | Mide CPU, memoria y FPS |
| `camvoice make-clips` | Genera clips sintéticos de prueba |
| `camvoice clean` | Vacía la caché de fotogramas |

Los subcomandos `run`, `bench` y `gui` aceptan además `--blur`, `--pixelate`, `--brightness`, `--contrast`, `--saturation`, `--vignette`, `--grain`, `--zoom` y `--no-effects`.

Con el ejecutable empaquetado, los subcomandos van en `camvoice-cli.exe` y la
interfaz en `camvoice.exe`.

## Generar el ejecutable

El ejecutable **no se versiona**: son 227 MB de contenido derivado. Se compila
en un par de minutos, con Python 3.12 y sin administrador:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt   # versiones exactas
.\scripts\build-portable.ps1          # pruebas + compilación + verificación + ZIP + SHA-256
```

Deja `dist\camvoice\` (con `instalacion\` y `docs\` dentro), `dist\camvoice-win64.zip`
y `dist\camvoice-win64.zip.sha256`. Es autocontenido: sin Python, sin OBS y sin
el Visual C++ Redistributable. GitHub Actions (`.github/workflows/build.yml`)
hace lo mismo en cada push y publica el Release al etiquetar `v*`. Detalles y
comprobaciones en
[docs/INSTALACION.md](docs/INSTALACION.md) seccion 2.

## Cómo funciona

Dos decisiones explican el rendimiento y la estabilidad:

**Los clips se decodifican una sola vez.** Cada clip se convierte a un archivo
de fotogramas en crudo, ya en la resolución, los FPS y el formato de píxel de
salida. En ejecución, obtener un fotograma es indexar un `memmap`: una copia de
memoria, sin decodificador en el bucle. El formato interno es NV12, que además
ocupa la mitad que BGR y permite calcular el fundido cruzado sobre los bytes
crudos sin error, porque YUV es una transformación afín de RGB.

**La decisión de "hablas o no" está separada del hardware.** `SpeechGate` y
`FrameComposer` son deterministas y no tocan audio, reloj ni cámara: reciben un
booleano o un estado y devuelven el siguiente estado o fotograma. Por eso todo
el comportamiento se verifica sin micrófono ni cámara, en 15 segundos.

Detalles en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Rendimiento medido

En un Intel de 4 núcleos a 2,5 GHz, 640 × 480 a 15 FPS, alimentando la cámara
virtual de verdad:

| | CamVoice (propia) | Filtro de OBS |
|---|---|---|
| FPS efectivos | 15,03 | 15,02 |
| CPU | 9,4 % de un núcleo = 1,2 % de la máquina | 10,6 % = 1,3 % |
| Memoria | estable | estable |
| Coste por fotograma (p95) | 5,03 ms | 2,05 ms |

Los 66,7 ms de presupuesto por fotograma a 15 FPS quedan usados al 7,5 % en el
peor de los dos casos.

Cifras completas y metodología en [docs/METRICS.md](docs/METRICS.md). Falta
repetirlo en el Core i5 de referencia y durante 30 minutos.

## Pruebas

```bash
pytest          # 341 pruebas, ~18 s, sin micrófono ni cámara
```

Procedimiento completo, incluidas las pruebas manuales, en
[docs/PRUEBAS.md](docs/PRUEBAS.md).

## Lo que esto NO hace

No sincroniza los labios. El clip de habla se reproduce cuando se detecta voz,
sin relación con las palabras: habrá desajuste entre el audio y la boca, por
construcción. El primer fonema nunca está sincronizado. Risas, toses y
carraspeos activan el estado de habla. **Quien se fije lo notará.**

El objetivo es estabilidad y bajo consumo, no una falsa promesa de
sincronización. Límites completos en [plan.md](plan.md) §5.

## Documentación

| | |
|---|---|
| [plan.md](plan.md) | Especificación, criterios de aceptación y estado |
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Diseño y decisiones técnicas |
| [docs/GUION-GRABACION.md](docs/GUION-GRABACION.md) | **Cómo grabar los dos clips: tiempos, qué decir, qué no hacer** |
| [docs/REQUISITOS.md](docs/REQUISITOS.md) | **Qué hace falta para probarlo en una máquina nueva** |
| [instalacion/00-LEEME.md](instalacion/00-LEEME.md) | **Scripts de instalación rápida y orden de ejecución** |
| [docs/CAMARA-VIRTUAL.md](docs/CAMARA-VIRTUAL.md) | La cámara virtual, independiente de OBS |
| [docs/INSTALACION.md](docs/INSTALACION.md) | Instalación, configuración, desinstalación |
| [docs/PRUEBAS.md](docs/PRUEBAS.md) | Procedimiento de pruebas |
| [docs/METRICS.md](docs/METRICS.md) | Mediciones de rendimiento |
| [docs/LICENCIAS.md](docs/LICENCIAS.md) | Dependencias y licencias |
