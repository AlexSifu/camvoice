# Arquitectura

Documento de referencia técnica (CA-14). Para el porqué de cada decisión frente
al plan original, ver `plan.md` §3 y §9.

## 1. Vista general

```
   micrófono                                            cámara virtual
       │                                                      ▲
       ▼                                                      │
  ┌─────────────────────┐                      ┌──────────────┴───────────┐
  │  HILO DE AUDIO      │                      │  HILO DE RENDER          │
  │                     │                      │                          │
  │  AudioCapture       │                      │  Pacer (reloj fijo)      │
  │    ↓ float32 mono   │                      │    ↓                     │
  │  Resampler → 16 kHz │                      │  FrameComposer           │
  │    ↓ tramas 20 ms   │                      │    ├─ ClipPlayer habla   │
  │  Detector (VAD)     │                      │    ├─ ClipPlayer silencio│
  │    ↓ bool/trama     │                      │    └─ fundido cruzado    │
  │  SpeechGate         │   VoiceState         │    ↓ fotograma NV12      │
  │    ↓                │ ───────────────────► │  VirtualCamSink          │
  │  VoiceStatus ───────┼──────┐               │    ↓                     │
  └─────────────────────┘      │               │  RenderStatus ───────┐   │
                               │               └──────────────────────┼───┘
                               ▼                                      ▼
                        ┌──────────────────────────────────────────────────┐
                        │  HILO DE UI (Qt)  — QTimer a 10 FPS              │
                        │  lee instantáneas, repinta, no genera nada       │
                        └──────────────────────────────────────────────────┘
```

Tres hilos propios, más el hilo de callback de PortAudio. Ni uno más.

La comunicación es **siempre en un sentido** y por **instantáneas inmutables**
(`@dataclass(frozen=True)`) protegidas por un cerrojo que solo se mantiene el
tiempo de reasignar una referencia. Ningún hilo espera a otro, así que no hay
forma de construir un interbloqueo ni de que la UI frene la salida.

## 2. Separación entre lógica y efectos

Las dos piezas que contienen el comportamiento del producto son **puras y
deterministas**:

| Módulo | Qué decide | Qué NO toca |
|---|---|---|
| `audio/gate.py` → `SpeechGate` | Cuándo se entra y se sale de SPEAKING | Audio, reloj, hilos |
| `video/composer.py` → `FrameComposer` | Qué fotograma sale en cada instante | Cámara, reloj, disco |

`SpeechGate.update(voiced: bool)` avanza su propio reloj en pasos de `frame_ms`.
`FrameComposer.step(voice_state)` avanza un fotograma de salida. Ninguna de las
dos consulta la hora del sistema.

Por eso el 100 % del comportamiento de detección y de reproducción se verifica
sin micrófono ni cámara virtual, de forma repetible y en menos de 10 segundos.
Los hilos (`pipeline.py`, `engine.py`) solo aportan el reloj, el hardware y la
recuperación de errores.

## 3. Cadena de audio

### 3.1. Captura

`sounddevice.InputStream` con callback. El callback **solo** hace mezcla a mono
y una copia a un `deque` acotado a 2 segundos. Nada más: es un hilo de tiempo
real de PortAudio y cualquier trabajo pesado ahí produce cortes.

Si la cola se llena se descarta el bloque más antiguo y se cuenta
(`dropped_blocks`), en lugar de crecer sin límite.

**Negociación de formato.** El dispositivo rara vez ofrece justo lo que se
quiere. Se prueba con `check_input_settings` —que no abre el dispositivo— en
este orden: la frecuencia del VAD (16 kHz), la nativa del dispositivo, 48 kHz,
44,1 kHz, 16 kHz; y 1, 2 o el máximo de canales. La primera combinación que el
dispositivo acepta es la que se usa.

**Resolución del dispositivo.** Los índices de PortAudio cambian al conectar o
desconectar hardware, así que lo que se guarda en la configuración es el
**nombre**; el índice se revalida en cada arranque y, si no corresponde, se
busca por nombre (exacto y luego laxo).

En Windows el mismo micrófono aparece hasta cuatro veces (MME, DirectSound,
WASAPI, WDM-KS). La lista ofrece **WASAPI primero**: da la latencia más baja y
la frecuencia nativa.

### 3.2. Remuestreo

`audio/resample.py`. FIR paso bajo (sinc enventanado con Hann, corte a
0,45 × fs_destino) seguido de interpolación lineal, **con estado entre bloques**:
se conservan la cola del filtro y la posición fraccionaria.

Sin ese estado aparecerían discontinuidades en cada costura de bloque, y el
detector las leería como transitorios: falsos positivos cada 10 ms. Hay una
prueba que trocea una rampa y verifica que sale monótona y sin escalones.

El FIR importa: 48 → 16 kHz sin filtrar refleja el contenido de 8-24 kHz dentro
de la banda de voz. Una prueba comprueba que un tono de 15 kHz se atenúa más de
25 dB.

### 3.3. Detección

Dos backends con la misma interfaz (`VoiceFrameDetector`), que devuelven un
`FrameDecision` por trama de 20 ms.

**`EnergyDetector`** — RMS en dBFS contra un piso de ruido adaptativo:

- El piso **sube despacio** (τ = 3 s) y solo cuando no se cree que hay voz, para
  que una frase larga no lo arrastre.
- El piso **baja rápido** (τ = 0,4 s), para notar en menos de un segundo que la
  sala se ha quedado en silencio.
- El piso está **acotado por abajo a −75 dBFS**. Sin ese límite se hunde hasta
  el silencio digital (−120 dBFS) y la relación señal/ruido deja de significar
  nada: un chasquido de −50 dBFS aparece como +70 dB de SNR. Se observó midiendo
  en un equipo real.
- Histéresis de trama: `snr_on_db` para activar, `snr_off_db` para desactivar.
- **Puerta absoluta** `gate_db` (−50 dBFS): por debajo, nunca hay voz.
- **Calibración** de 300 ms al arrancar en la que nunca se reporta voz, para que
  el piso parta de una medida real y no del valor inicial.

**`WebrtcDetector`** — WebRTC VAD (mezclas gaussianas sobre seis sub-bandas, con
coeficientes fijos; no es un modelo entrenable ni generativo), con el
`EnergyDetector` **por delante** como puerta de ruido y como fuente del nivel y
del piso para la interfaz. La puerta es necesaria: WebRTC marca voz con señales
muy débiles, y sin ella un micrófono silenciado activaría el estado de habla.

### 3.4. Histéresis temporal

`SpeechGate`. Convierte el tren de bits del VAD —que parpadea varias veces por
segundo entre palabras y en consonantes sordas— en dos estados estables.

```
                     voz acumulada ≥ attack_ms
                     Y ciclo de voz ≥ 60 %
                     Y tiempo en SILENT ≥ min_silent_ms
        SILENT ──────────────────────────────────────────► SPEAKING
               ◄──────────────────────────────────────────
                     silencio continuo ≥ release_ms
                     Y tiempo en SPEAKING ≥ min_speaking_ms
```

La **guarda de ciclo de trabajo** (60 %) es la pieza no obvia. Histéresis y
tolerancia a pausas, implementadas literalmente como pedía el plan, se
contradicen: con una tolerancia generosa, los positivos sueltos y espaciados del
ruido de fondo suman `attack_ms` por acumulación lenta y la puerta se abre sola.
Exigir que al menos el 60 % de la ventana de acumulación sea voz distingue "dos
sílabas separadas por un hueco" (ciclo alto) de "ruido que da positivo una trama
de cada cinco" (ciclo bajo).

### 3.5. Ajuste en caliente

El detector guarda una **referencia al mismo objeto `VadConfig`** que edita la
interfaz, así que mover un deslizador de umbral surte efecto en la siguiente
trama, sin reconstruir nada y sin recalibrar.

Solo `backend`, `frame_ms` y `aggressiveness` obligan a reconstruir el detector,
lo que implica una recalibración de 300 ms. `apply_vad_changes()` distingue los
dos casos.

## 4. Cadena de video

### 4.1. Caché de fotogramas (la decisión que fija el rendimiento)

Decodificar H.264 en el bucle de salida no solo cuesta CPU: cuesta de forma
**irregular**. Un fotograma clave cuesta un orden de magnitud más que un
intermedio, lo que produce un pico justo cada vez que el bucle vuelve al
principio.

Cada clip se convierte **una vez** a un archivo de fotogramas en crudo, ya en la
resolución, los FPS, el encuadre, el espejo y el formato de píxel de salida:

```
  %APPDATA%\camvoice\clipcache\
      habla-prueba-a1b2c3d4e5f6g7h8i9j0.raw     fotogramas consecutivos
      habla-prueba-a1b2c3d4e5f6g7h8i9j0.json    ClipMeta
```

En ejecución, `ClipCache.frame(i)` devuelve `memmap[i]`: una **vista sin copia**
sobre el archivo. Coste constante, sin decodificador en el bucle.

**Identidad de la caché.** Hash SHA-1 de: ruta absoluta, tamaño y `mtime_ns` del
archivo original, resolución, FPS, modo de encuadre, espejo, formato de píxel y
color de barras. Cambiar cualquiera de esas cosas —o sustituir el video por otro
con el mismo nombre— invalida la caché automáticamente.

**Escritura atómica.** Se escribe a `.raw.tmp` y se renombra. Si el proceso
muere a mitad, no queda una caché parcial que parezca válida. Además, al abrir
se comprueba que el tamaño del `.raw` coincide con `frame_count × frame_bytes`;
si no, se regenera.

**Remuestreo temporal.** Para cada índice de salida *k* se toma el fotograma
fuente ⌊*k* · fps_fuente / fps_salida + 0,5⌋ (vecino más cercano), avanzando el
lector secuencialmente. Esto tolera que el contenedor no declare el número de
fotogramas: se para al agotar la fuente. Si la cadencia declarada no es creíble
(0, NaN, > 240) se asume 25 FPS.

**Topes.** `max_cache_mb` por clip (1536 MB por defecto) y 300 s absolutos.
Cuando se recorta, el metadato `truncated` lo registra y la interfaz lo avisa.

### 4.2. Formato interno NV12

La cámara virtual de OBS es **NV12 nativa** (verificado:
`pyvirtualcam.Camera.native_fmt == NV12`). Guardando la caché ya en NV12:

- **Cero conversiones de color en el bucle.** El fotograma sale del `memmap` y
  entra en la cámara tal cual.
- **La mitad de bytes** que BGR: 460.800 frente a 921.600 a 640 × 480.
- **El fundido cruzado sigue siendo correcto sobre los bytes crudos**, porque
  YUV es una transformación afín de RGB:

  ```
  α·(M·RGB₁ + c) + (1−α)·(M·RGB₂ + c) = M·(α·RGB₁ + (1−α)·RGB₂) + c
  ```

  Mezclar en YUV y mezclar en RGB dan el mismo resultado. Y como el submuestreo
  de croma también es lineal, mezclar croma submuestreado equivale a submuestrear
  el croma mezclado. `tests/test_cache.py` comprueba la equivalencia
  numéricamente.

El plano UV de NV12 se construye a mano al preparar (OpenCV solo ofrece I420,
con U y V en planos separados), una vez por fotograma y nunca en el bucle.

El formato `bgr` sigue disponible por configuración, para backends de cámara
virtual cuyo formato nativo no sea NV12.

### 4.3. Reproducción en bucle

`ClipPlayer` lleva una posición en coma flotante y una dirección.

**Modo `pingpong` (por defecto).** Al llegar al final se reproduce hacia atrás.
Elimina la costura del bucle sin pedirle al usuario que grabe un clip cuyo final
empalme con su principio: una cara hablando se ve natural en reversa. Una prueba
verifica que el índice **nunca salta más de un fotograma**, lo que es la
definición operativa de "sin costura".

Con velocidades muy altas el rebote puede cruzar varios extremos en un paso; se
reflejan hasta 8 veces y, más allá, se ancla en un extremo en lugar de divergir.

### 4.4. Compositor

`FrameComposer` mantiene la máquina de estados visual:

- **TRANSITION** dura `round(transition_ms × fps / 1000)` fotogramas de salida.
  Se mezcla con `cv2.addWeighted` sobre un búfer reutilizado (sin asignar en el
  bucle) y peso suavizado por *smoothstep* (`t²(3−2t)`), que elimina el "pop"
  del arranque y el final de un fundido lineal.
- **Inversión a mitad.** Si el estado cambia durante la transición, el progreso
  se invierte (`step ← steps − step`) y se intercambian origen y destino, en
  lugar de empezar de cero. Visualmente, el fundido se deshace desde donde iba.
- **Política de posición del clip de habla.** Durante SILENT el reproductor de
  habla se pausa; tras `resume_window_ms` (1,5 s) se **rebobina al primer
  fotograma**, que por las recomendaciones de grabación es una pose con la boca
  cerrada. Si el habla vuelve antes, continúa donde iba.
- **El clip de silencio nunca se pausa.** Avanza siempre, incluso mientras se
  habla, para que parpadeos y respiración no se congelen.

### 4.5. Cadena de efectos

`video/effects.py`. Se aplica al fotograma de salida, después de componer. Los
parámetros son **porcentajes derivados del ancho del fotograma**, no píxeles
absolutos: así el 60 % de desenfoque se ve igual a 320x240 que a 720p y el
usuario no tiene que reajustar cada vez que cambia la resolución.

Orden: zoom → color → pixelado → desenfoque → grano → viñeta. No es arbitrario;
el razonamiento de cada paso está en el docstring del módulo.

Con el formato interno NV12 hace falta ir y volver a BGR, porque desenfocar el
plano UV entrelazado mezclaría las componentes U y V y produciría manchas de
color. Cuesta unos 1,5 ms a 640x480. Si no hay ningún efecto activo **no se
convierte nada**: `EffectChain.active` es falso y el motor devuelve la misma
vista del `memmap`.

#### Tres optimizaciones que decidieron si la función era viable

La primera versión funcionaba y consumía **76 % de un núcleo**, cuatro veces el
objetivo. Se midió, se encontró el porqué, y bajó a **13,8 %**:

| Cambio | Antes | Después |
|---|---|---|
| Desenfoque: reducir, filtrar con sigma/k, ampliar | 20 ms | 2,4 ms |
| Grano y viñeta con `addWeighted`/`multiply` en vez de ida y vuelta a float32 en NumPy | 9 ms | 1,6 ms |
| `cv2.setNumThreads(1)` durante el render | 12,5 CPU-ms | 4,2 CPU-ms |

La tercera es la menos obvia y la más instructiva. Medido con la cadena completa
a 640x480:

```
  hilos   ms/fotograma   CPU-ms/fotograma
      1       4.44             4.22
      8       4.39            12.50
```

A este tamaño de imagen **la paralelización de OpenCV no mejora el tiempo de
pared** —repartir el trabajo cuesta tanto como lo que ahorra— pero **triplica la
CPU consumida**. Y aquí sobran 60 ms de los 66,7 de presupuesto por fotograma,
mientras que la aplicación de videollamada sí necesita CPU para codificar su
propio video. Cambiar tiempo de pared que no se usa por CPU que hace falta en
otro sitio es un mal negocio.

La preparación de clips es el caso contrario —un lote de una vez, con el usuario
esperando delante— así que ahí se usan todos los hilos.

### 4.6. Reloj

`Pacer`. El instante objetivo se **acumula** (`next += 1/fps`), no se duerme un
delta en cada vuelta: así el error de cada espera no se suma y en 30 minutos la
cadencia media se mantiene exacta.

Un retraso de más de 3 periodos **reancla** el reloj y se cuenta como
resincronización, en lugar de intentar recuperar emitiendo una ráfaga de
fotogramas (que la aplicación receptora vería como un tirón).

`hires_timer` sube la resolución del temporizador de Windows a 1 ms mientras
corre el motor. Python 3.11+ ya usa temporizadores de alta resolución en
`time.sleep`, pero esto cubre versiones anteriores y entornos donde otra
librería la haya bajado.

### 4.7. Salida

`VirtualCamSink` envuelve `pyvirtualcam.Camera`. Traduce los fallos conocidos a
mensajes accionables (no instalada / en uso por otra aplicación) y reabre con
espera creciente.

## 4.8. Grabación guiada

`record.py`. El guion es **datos, no código**: `RecordingPlan` describe las
señales con sus tiempos y la interfaz solo pregunta «¿qué toca en el segundo
t?». Así el guion completo se verifica sin cámara, incluido que las señales
cubran todo el clip sin huecos.

Existe para que las condiciones que de verdad importan las imponga el programa
en vez de confiar en que el usuario recuerde un documento: el medio segundo
inicial con la boca cerrada, la duración en el rango útil y el cierre de boca
al final.

### Dos problemas que solo aparecieron al probarlo con una cámara real

**La cadencia declarada miente.** Una webcam que anuncia 30 FPS entregaba 10 con
poca luz: baja la cadencia para exponer más tiempo. Escribiendo 30 en la
cabecera del archivo, un clip de 5 s quedaba de 1,7 s y se reproducía al triple
de velocidad, con la boca moviéndose de forma absurda. Y el problema se
arrastraba: el paso de preparación remuestrea usando los FPS **declarados** en
el archivo. Ahora la cadencia se **mide durante la cuenta atrás** y es la medida
la que se escribe.

**Nuestra propia cámara virtual aparecía como fuente de grabación.** El camino
obvio —cruzar el índice de OpenCV con el nombre del registro de Windows— no
funciona: la clave `Instance` del registro contiene solo los filtros
**software**, no las webcams físicas, así que el emparejamiento por posición
mezclaba unas con otras. Una webcam real salía etiquetada como «Cámara 3» y una
virtual podría haberse ofrecido, creando un bucle.

La solución no depende de nombres: se leen **dos fotogramas separados 250 ms** y
se comparan bit a bit. Una cámara virtual sin productor emite un fotograma de
relleno congelado; **un sensor real nunca entrega dos fotogramas idénticos**,
porque siempre hay ruido de lectura. Es un discriminador exacto, sin umbral que
ajustar.

## 4.9. Bandeja del sistema

`ui/tray.py`. El icono se **dibuja en código** y no se empaqueta un archivo,
porque así el icono *es* el indicador de estado: cambia de color con
SILENT/SPEAKING/TRANSITION y no hay cuatro PNG que mantener sincronizados con la
lógica.

Con `ui.minimize_to_tray`, cerrar la ventana la esconde en vez de terminar: la
sesión sigue alimentando la cámara virtual. Es minimización a bandeja estándar,
**no evasión**: el proceso sigue en el Administrador de tareas, el icono sigue
junto al reloj, y el nombre «CamVoice Virtual Camera» sigue en la lista de
cámaras del programa de videollamada.

## 5. Política ante errores

El principio es que **nada que falle debe detener la salida de video**. En una
videollamada, una imagen congelada se interpreta como una conexión mala; un
proceso caído o una pantalla negra, como una cámara averiada.

| Fallo | Respuesta |
|---|---|
| Micrófono desconectado | Hilo de audio detecta `stream.active == False`, fuerza SILENT, publica mensaje, reintenta a 0,5 / 1 / 2 / 4 s |
| Micrófono silenciado en Windows | Tras 5 s bajo la puerta absoluta, aviso explícito con la ruta de Configuración de Windows |
| Cámara virtual no instalada | Mensaje con la ruta del `.bat` de instalación de OBS; el motor sigue y la vista previa funciona |
| Cámara virtual en uso | Mensaje que nombra la causa (OBS abierto) en lugar de un error genérico |
| Cámara virtual cae en marcha | Se cambia a `NullSink`, se publica el error y se reintenta con espera creciente |
| Caché de clip ilegible | Se congela el último fotograma bueno; nunca negro |
| Configuración corrupta | Se arranca con valores por defecto y se conserva el archivo como `.json.bad` |
| Video no abrible | Se valida al seleccionar en la interfaz, antes de procesar |

## 6. Rutas en disco

Todo bajo `%APPDATA%\camvoice\` (o `$CAMVOICE_HOME` si está definida, que es lo
que usan las pruebas para no tocar la instalación real):

```
  config.json                 configuración, escrita de forma atómica
  config.json.bad             la última configuración corrupta, si hubo
  clipcache/*.raw|.json       caché de fotogramas
  metrics/metrics-*.csv       una fila por muestra de cada medición
```

## 7. Módulos

| Archivo | Responsabilidad |
|---|---|
| `config.py` | `AppConfig` y secciones, validación con mensajes, persistencia |
| `controller.py` | `Session`: prepara clips, arranca/para los hilos, compatibilidad entre clips |
| `metrics.py` | `PerfMonitor`: muestreo, CSV, percentiles, pendiente de memoria |
| `cli.py` | `doctor`, `devices`, `probe`, `prepare`, `run`, `bench`, `vad-test`, `make-clips`, `config`, `clean`, `gui` |
| `testclips.py` | Generador de clips sintéticos |
| `record.py` | Guion de grabación (datos), grabador, detección de cámaras |
| `audio/capture.py` | PortAudio, negociación, cola acotada, resolución de dispositivo |
| `audio/resample.py` | FIR + interpolación con estado, mezcla a mono, dBFS |
| `audio/vad.py` | `EnergyDetector`, `WebrtcDetector`, fábrica |
| `audio/gate.py` | `SpeechGate` (puro) |
| `audio/pipeline.py` | Hilo de audio, recuperación, `VoiceStatus` |
| `video/cache.py` | Preparación, `memmap`, geometría, NV12 |
| `video/clip.py` | `ClipPlayer` (puro) |
| `video/composer.py` | `FrameComposer` (puro) |
| `video/effects.py` | Cadena de efectos (pura), límite de hilos de OpenCV |
| `video/engine.py` | `Pacer`, hilo de render, vista previa, `RenderStatus` |
| `output/sink.py` | `VirtualCamSink`, `NullSink`, diagnóstico |
| `ui/app.py` | Ventana PySide6 con pestañas |
| `ui/widgets.py` | Widgets reutilizables y hoja de estilo |
| `ui/recorder_tab.py` | Pestaña de grabación guiada |
| `ui/tray.py` | Icono de bandeja con estado |
