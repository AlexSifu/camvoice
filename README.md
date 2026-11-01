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

## Reuniones largas y gestos (modo director)

Por defecto la reproducción usa el **modo director**
([docs/DISENO_CAMVOICE_TIEMPO_REAL.md](docs/DISENO_CAMVOICE_TIEMPO_REAL.md)):

- **Reproducción continua**: cada clip avanza fotograma a fotograma, sin
  saltos dentro del clip. Los cambios (hablar/callar, renovar segmento, gesto)
  esperan a un fotograma «ancla» (boca cerrada, pose neutra) hasta 180 ms; si
  no llega, o el clip tiene pocas anclas, se hace un fundido corto de
  150-200 ms.
- **Micro-pausas**: un silencio de 150-250 ms dentro de una frase lleva al
  grupo de silencio por un ancla. No es sincronía labial: la boca no sigue
  las palabras.
- **Varios clips** de silencio y de habla (pestaña Videos → biblioteca) para
  que en 1-2 horas no se reconozca un bucle; se alinean en encuadre y color
  con el silencio principal.
- **Gestos** (saludar, sonreír, asentir…): `Ctrl+Alt+F1…F8`, cancelar con
  `Ctrl+Alt+F12`, botones de la pestaña Gestos o menú de la bandeja. Si pides
  un gesto mientras hablas se usa su variante `<gesto>_hablando` si la hay; si
  no, espera como mucho 170 ms a una micro-pausa o a un ancla, entra con un
  fundido corto y al terminar vuelve al habla. Nunca se rechaza ni se aplaza.
- **Frases breves automáticas**: si importaste las escenas 6, 7 y 8, el
  director abre a veces una intervención con «Respuesta corta» o «Frase
  corta» y usa «Terminar una explicación» como cierre en intervenciones
  largas. Si sigues hablando enlaza con el bucle de habla; si callas antes,
  pasa al silencio con un fundido corto. Un gesto pedido la sustituye al
  instante. Se desactiva con `gestures.auto_speaking_shots: false`.
- **Rotación**: en silencios largos alterna «Silencio neutro» (1) y «Escucha
  atenta» (3); en intervenciones largas rota entre «Habla natural» (2) y las
  variaciones de habla, entrando por anclas distintas, no siempre por el
  principio.
- **Pausar video** (`Ctrl+Alt+F11`, pestaña Gestos o bandeja): congela la
  última imagen emitida (nunca negro, sin ningún signo de pausa en la cámara)
  y lo indica en la ventana y en la bandeja; al reanudar vuelve con un
  fundido hacia el ancla más parecida.

La imagen emitida por la cámara virtual es siempre limpia: sin rótulos,
marcas de agua, bordes ni textos. Los estados (silencio, habla, gesto, pausa)
y el aviso «VÍDEO PREGRABADO» se muestran solo en la ventana local y en la
bandeja.
Cómo grabar el material: [docs/GUIA_GRABACION_Y_GESTOS.md](docs/GUIA_GRABACION_Y_GESTOS.md).
El modo anterior sigue disponible (`playback: classic`).

## Biblioteca de 62 grabaciones (pestaña Biblioteca)

Si grabaste las 62 escenas numeradas con la app Cámara de Windows (carpeta
`Imágenes\Camera Roll`, archivos `1.mp4` … `66.mp4`; los números 50, 52, 59 y
63 se omiten a propósito):

1. Pestaña **Biblioteca** → «Detectar 62 grabaciones». Muestra cuáles están,
   cuáles faltan y cuántos archivos se ignoran (sin número, `v_aud_*`,
   subcarpetas, no-video).
2. «**Validar y preparar vídeos**»: valida cada archivo (abre, lee cadencia,
   resolución y duración, genera miniatura), importa las escenas a
   `%APPDATA%\camvoice\library\manifest.json` y prepara cachés e índices.
   Muestra fase, progreso, tiempo transcurrido y un informe; se puede
   cancelar y retomar (lo ya validado no se repite). Por defecto **no copia**
   los videos: los referencia en su carpeta. Todo archivo que se decodifica
   queda «Listo» y activo de inmediato; los avisos (duración corta, cadencia
   variable, otra resolución) se muestran en el informe pero no bloquean.
   Solo un archivo vacío o ilegible queda en «Error».
3. Tabla con filtros por categoría y estado y búsqueda; al seleccionar una
   escena se puede reproducir. Con los videos preparados, los botones de
   **ensayo** reproducen hablar→callar, callar→hablar, gesto→silencio y
   gesto→habla y muestran el par de fotogramas elegido, la espera hasta el
   fundido y si se usó el fundido de reserva.
4. Opciones avanzadas (plegadas): copiar en vez de referenciar, conservar los
   clips principales, revalidar todo, no preparar cachés y **deshacer la
   última importación**.

La pestaña Gestos muestra la galería de escenas de acción (miniatura,
descripción, atajo opcional y «Probar»). Las escenas no traen atajo global:
no se registran 62 combinaciones, solo las que asignes; se rechazan las
repetidas. Se marca qué gestos no tienen variante «hablando».

Desde la consola: `camvoice library --import` y después `camvoice index`;
`camvoice transitions` mide las transiciones con los clips preparados.

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
| `camvoice index` | Prepara cachés, normalización e índices del modo director |
| `camvoice gestures` | Lista y valida los gestos; `--set saludar clip.mp4 --hotkey Ctrl+Alt+F1` asigna uno |
| `camvoice library` | Detecta y valida las 62 grabaciones numeradas; `--import` las importa, `--rollback` deshace |
| `camvoice transitions --repeats 20` | Ensaya hablar/callar/gesto con los clips preparados y resume esperas, fundidos y coste de discontinuidad |
| `camvoice soak --simulate --duration 7200` | Simula 2 h de reunión (voz sintética y gestos) sin micrófono ni cámara |
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
pytest          # 601 pruebas (+5 omitidas sin cámara real), ~2,5 min, sin micrófono ni cámara
```

Procedimiento completo, incluidas las pruebas manuales, en
[docs/PRUEBAS.md](docs/PRUEBAS.md).

## Lo que esto NO hace

No sincroniza los labios. En modo director los clips de habla avanzan de
forma continua y solo se pasa a boca cerrada en las micro-pausas (por un
fotograma ancla), así que la boca no sigue las palabras. El primer fonema nunca está sincronizado. Risas, toses y
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
| [docs/DISENO_CAMVOICE_TIEMPO_REAL.md](docs/DISENO_CAMVOICE_TIEMPO_REAL.md) | Modo director: estados, boca guiada, gestos, transiciones |
| [docs/GUIA_GRABACION_Y_GESTOS.md](docs/GUIA_GRABACION_Y_GESTOS.md) | **Material para reuniones largas y gestos, atajos** |
| [docs/VALIDACION_CAMVOICE.md](docs/VALIDACION_CAMVOICE.md) | Pruebas ejecutadas y resultados medidos |
