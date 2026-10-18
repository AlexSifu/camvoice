# Especificación técnica: Cámara virtual con reproducción de video según actividad de voz

> **Versión 2 — plan revisado contra la implementación.**
> La v1 describía el objetivo correctamente. Esta versión conserva todos sus
> requisitos y añade lo que faltaba: las decisiones técnicas ya verificadas en
> este equipo, cuatro huecos del plan original que habrían aparecido durante la
> integración, y criterios de aceptación medibles donde antes había
> afirmaciones no comprobables.
> El apartado [§9](#9-cambios-respecto-a-la-v1) resume qué cambió y por qué.

---

## 1. Objetivo del proyecto

Desarrollar una aplicación de escritorio para Windows que permita utilizar un
video pregrabado del usuario como fuente de una cámara virtual.

La aplicación captura el audio del micrófono en tiempo real y detecta cuándo el
usuario está hablando. Cuando detecta voz, reproduce un video pregrabado del
usuario hablando. Cuando detecta silencio, reproduce un video del usuario con la
boca cerrada o en una posición neutral.

El objetivo es una experiencia visual suficientemente natural para una cámara
virtual, con bajo consumo de recursos y sin GPU dedicada.

**Restricciones fundamentales** (sin cambios respecto a la v1):

- No utilizar LLMs.
- No utilizar modelos generativos de IA.
- No utilizar servicios externos ni APIs de pago.
- No requerir conexión a Internet durante la ejecución.
- No entrenar modelos de machine learning.
- No requerir GPU dedicada ni CUDA.
- No utilizar avatares, personajes 3D ni rostros sintéticos.
- Priorizar una implementación sencilla, mantenible y eficiente.

### 1.1. Lo que esta aplicación NO hace con el audio

Aclaración que faltaba en la v1 y que evita una expectativa equivocada: **esta
aplicación no enruta, graba ni retransmite audio.** Solo lo analiza para decidir
entre dos estados, y descarta cada trama tras analizarla.

En una videollamada, la aplicación de videoconferencia sigue tomando el
micrófono real por su cuenta. Esta aplicación solo aporta el video. El audio de
los propios clips se ignora por completo.

---

## 2. Funcionamiento esperado

### 2.1. Configuración inicial

El usuario debe poder seleccionar:

- Un dispositivo de entrada de audio.
- Un video de referencia con el usuario hablando.
- Un video de referencia con el usuario en silencio y la boca cerrada.
- La cámara virtual de salida, si existen varias opciones.
- La resolución y los FPS de salida.

Los videos deben grabarse preferiblemente con la misma cámara, iluminación,
fondo, encuadre y posición del rostro.

**Añadido en la v2 — recomendaciones de grabación que el software no puede
suplir.** La calidad del resultado depende más de los clips que del código, así
que estas condiciones deben estar documentadas de cara al usuario:

| Recomendación | Motivo |
|---|---|
| El **primer fotograma de los dos clips** debe tener la boca cerrada y la mirada al frente | Es la pose desde la que se reanuda el habla tras un silencio largo (ver [§2.3](#23-reproducción-del-video)) |
| Duración de **8 a 20 s** por clip | Menos se nota como bucle; más consume caché sin aportar variedad |
| Grabar los dos clips **seguidos, sin mover nada** | Cualquier cambio de postura, encuadre o luz lo tiene que absorber una transición de 160 ms, y no puede |
| **Misma resolución y relación de aspecto** en ambos | Evita que el modo de encuadre recorte cada clip de forma distinta |
| Mirada **al objetivo**, sin gestos amplios ni manos en plano | Un gesto grande interrumpido a mitad por una transición es lo más delatador |
| Postura **neutra y estable** en el clip de silencio | Es el que más tiempo está en pantalla |

La aplicación comprueba lo que es medible (resolución, cadencia, relación de
aspecto, duración) y avisa de las discrepancias; el resto es responsabilidad de
la grabación.

### 2.2. Detección de voz

Detección de actividad de voz mediante análisis directo de la señal, sin
reconocimiento del contenido hablado.

Se usan dos detectores con la misma interfaz:

- **WebRTC VAD** (predeterminado): modelo de mezclas gaussianas sobre
  sub-bandas, incluido en el proyecto WebRTC de Google. No es un modelo
  entrenable ni generativo: son coeficientes fijos y un clasificador de seis
  sub-bandas. Es muy superior al nivel de energía distinguiendo voz de ruido
  estacionario (ventilador, aire acondicionado, ruido de calle).
- **Energía RMS con piso de ruido adaptativo**: alternativa sin dependencias
  nativas, también el camino de reserva si WebRTC no está disponible.

**Añadido en la v2:** el detector WebRTC lleva **por delante la puerta de
energía** del detector RMS. WebRTC marca voz con señales muy débiles —un
micrófono silenciado o tapado da positivos— y sin esa puerta absoluta el
sistema se activaría solo.

El detector distingue:

- Silencio.
- Voz activa.
- Ruido de fondo moderado (absorbido por el piso de ruido adaptativo: sube
  despacio, con constante de 3 s, y baja rápido, con constante de 0,4 s).

Controles expuestos, todos ajustables en caliente:

| Control | Efecto |
|---|---|
| `aggressiveness` (0-3) | Exigencia del VAD de WebRTC |
| `snr_on_db` / `snr_off_db` | Umbrales de activación y desactivación sobre el piso de ruido (histéresis de trama) |
| `attack_ms` | Voz continua necesaria para activar. **Domina la latencia percibida** |
| `release_ms` | Silencio continuo necesario para desactivar (hangover) |
| `pause_tolerance_ms` | Pausas cortas al hablar que no reinician la acumulación |
| `min_speaking_ms` / `min_silent_ms` | Permanencia mínima en cada estado |
| `gate_db` | Puerta absoluta: por debajo, nunca hay voz |

No es necesario transcribir ni comprender el audio.

#### 2.2.1. Añadido en la v2: la guarda de ciclo de trabajo

El plan original pedía histéresis y tolerancia a pausas. Implementados tal cual,
**se contradicen**: con una tolerancia a pausas generosa, los positivos sueltos
y espaciados que produce el ruido de fondo acaban sumando `attack_ms` por
acumulación lenta, y la puerta se abre sola. Es un fallo que no aparece en
pruebas cortas y sí a los veinte minutos de una reunión.

La solución implementada: para activar, además de acumular `attack_ms` de voz,
**al menos el 60 % de la ventana de acumulación debe ser voz**. Eso distingue
"dos sílabas separadas por un hueco" (ciclo alto) de "ruido que da positivo una
trama de cada cinco" (ciclo bajo), sin pedirle nada al usuario.

Hay una prueba automatizada dedicada a este caso: 30 s de un positivo cada
100 ms no deben producir ni una sola activación.

### 2.3. Reproducción del video

Máquina de estados con tres estados:

**SILENT**
- Reproduce el video neutral.
- Mantiene una imagen estable cuando no hay voz.
- Evita transiciones innecesarias.

**SPEAKING**
- Reproduce el video del usuario hablando.
- Velocidad de reproducción configurable.
- No reinicia el video con cada pequeño cambio del detector.

**TRANSITION**
- Fundido cruzado de duración configurable (160 ms por defecto) con suavizado
  en los extremos (*smoothstep*), para que no se perciba el arranque y el final
  del fundido.
- Sin parpadeos, pantallas negras ni congelamientos.
- Una transición interrumpida a mitad **se deshace desde donde iba**, en lugar
  de saltar: si el habla vuelve mientras se está fundiendo hacia el silencio,
  el progreso se invierte conservando el valor actual.

**Añadido en la v2 — tres decisiones que el plan dejaba abiertas:**

1. **Bucle de ida y vuelta (*pingpong*) por defecto.** El salto del último al
   primer fotograma de un bucle normal es un corte visible cada 8 s, y pedirle
   al usuario que grabe un clip cuyo final empalme con su principio no es
   razonable. Reproducir hacia atrás al llegar al final elimina la costura: una
   cara hablando se ve natural en reversa. Hay una prueba que verifica que el
   índice nunca salta más de un fotograma.

2. **Política de posición del clip de habla.** El plan pedía "recordar la
   posición cuando sea conveniente" pero "evitar que la boca permanezca abierta
   durante silencios prolongados". Lo implementado: si el habla vuelve antes de
   `resume_window_ms` (1,5 s), el clip continúa donde iba; si no, se rebobina al
   primer fotograma, que por las recomendaciones de grabación es una pose con la
   boca cerrada.

3. **El clip de silencio nunca se pausa.** Sigue avanzando incluso mientras se
   habla, para que parpadeos y respiración no se congelen y la vuelta al
   silencio no reanude una pose antigua.

### 2.4. Cámara virtual

La salida debe poder utilizarse como una cámara virtual de Windows, **sin
depender de OBS**: la aplicación tiene que ser autónoma, aparecer como una
cámara y funcionar.

**El límite que lo condiciona todo.** En Windows una cámara no es un proceso: es
una DLL que la aplicación receptora carga **dentro de su propio proceso**. Teams
no habla con nuestro programa, carga un filtro registrado en el sistema. De ahí
se sigue que **ninguna aplicación en Python puede ser por sí misma una cámara de
Windows**; hace falta una DLL nativa. Lo que sí puede hacer Python es
*alimentarla* por memoria compartida, que es lo que hace `pyvirtualcam`.

La pregunta, por tanto, es **qué DLL y quién la registra**:

**Resuelto: la aplicación es autónoma.** Incluye su propio filtro DirectShow
(`UnityCaptureFilter`, licencia MIT, que pese al nombre no requiere ni Unity ni
OBS) y lo registra con `camvoice-cli install-camera`. Aparece como **«CamVoice
Virtual Camera»**.

El nombre no obligó a compilar nada: el filtro lo acepta como parámetro de
registro (`regsvr32 "/i:UnityCaptureName=..."`), lo que eliminó la única razón
de peso para mantener un filtro propio en C++.

| Opción | Quién registra | Nombre visible | Dependencia | Estado |
|---|---|---|---|---|
| **A. Filtro propio incluido** | Nuestro instalador | «CamVoice Virtual Camera» | **Ninguna** | **Implementado y verificado** |
| B. Filtro de OBS | El instalador de OBS | «OBS Virtual Camera» | Instalar OBS | Implementado, como alternativa automática |
| C. Filtro compilado a medida | Nuestro instalador | el que queramos | Ninguna | Descartado: ya no aporta nada salvo CLSID propio |

La selección es automática (`output.backend = "auto"`): se enumera el registro
de Windows en las vistas de 64 y 32 bits y **se prefiere el filtro propio**, de
modo que no compite con OBS ni lo necesita, pero lo usa si es lo único presente.

Verificado en este equipo: registro correcto en las dos arquitecturas y 60 s
alimentándola a **15,03 FPS con 9,4 % de un núcleo**, sin OBS.

Se descartan: montar escenas en OBS, automatizar su interfaz, y escribir un
controlador de kernel (que exige firma de controlador). También se descarta
Media Foundation por ahora: es Windows 11 22H2+ y exige firma de código.

El análisis completo y lo que falta para cerrarlo está en
**`docs/CAMARA-VIRTUAL.md`**.

La salida es continua, con resolución configurable, por defecto 640 × 480 a
15 FPS, conservando la relación de aspecto sin deformar el rostro.

Modos de encuadre, porque "sin deformar, sin barras y sin recortes" es
imposible de cumplir a la vez cuando la relación de aspecto del clip no coincide
con la de salida:

| Modo | Comportamiento | Cuándo |
|---|---|---|
| `cover` (por defecto) | Recorte centrado para llenar el marco | No deforma, no deja barras; se pierde el borde del encuadre |
| `contain` | Cabe entero, con barras | No deforma, no recorta |
| `stretch` | Deforma | Solo si el clip ya tiene una relación rara |

El modo activo y su consecuencia se avisan antes de arrancar, y el recorte se ve
en la vista previa. Nunca es un recorte inesperado.

#### 2.4.1. Cuatro límites del mecanismo, sea cual sea el filtro

Son propiedades de los filtros DirectShow, no defectos de esta aplicación, pero
producen síntomas que parecen fallos y deben estar documentados:

- **Es un filtro DirectShow.** Teams, Zoom, Meet, Discord, OBS y cualquier
  aplicación basada en Chromium lo ven. Las que solo usan Media Foundation —como
  la app **Cámara de Windows**— no. No sirve como prueba de que la salida
  funciona.
- **32 frente a 64 bits.** Un filtro solo se carga en procesos de su misma
  arquitectura: una aplicación de videollamada de 32 bits **únicamente** ve un
  filtro de 32 bits. Por eso hay que registrar las dos DLL, y por eso
  `camvoice-cli cameras` avisa si falta una.
- **Admite un solo productor a la vez.** Si otra aplicación está alimentando el
  mismo filtro (OBS con su cámara virtual iniciada, por ejemplo), esta no puede
  tomarlo. Se detecta el caso y se explica en lugar de dar un error genérico.
  Un filtro propio con CLSID propio (opción C) elimina esta colisión.
- **Las aplicaciones enumeran las cámaras al arrancar.** La primera vez hay que
  cerrar y volver a abrir la aplicación receptora para que aparezca.
- **Registrar un filtro exige permisos de administrador**, una sola vez, porque
  escribe en HKLM. No exige firma de controlador.

### 2.5. Interfaz de usuario

Interfaz sencilla que permite:

- Seleccionar los videos de referencia.
- Seleccionar el micrófono.
- Configurar sensibilidad y tiempos de detección.
- Iniciar y detener el procesamiento.
- Activar y desactivar la salida virtual.
- Visualizar una previsualización.
- Mostrar el estado actual: silencio, voz detectada o transición.
- Mostrar errores de configuración y dispositivos no disponibles.

**Añadido en la v2 — qué se puede cambiar en caliente y qué no.** El plan daba a
entender que todo era ajustable en marcha. No lo es, y fingirlo sería peor que
decirlo:

| En caliente | Requiere volver a iniciar |
|---|---|
| Micrófono, sensibilidad y todos los tiempos del detector | Archivos de los clips |
| **Todos los efectos** (desenfoque, pixelado, color, zoom, grano, viñeta) | — |
| Transición, velocidades, modo de bucle | Resolución, FPS |
| Activar/desactivar la cámara virtual | Encuadre, espejo |

Los controles del segundo grupo invalidan la caché de fotogramas, así que la UI
los **deshabilita mientras la sesión corre** y lo explica, en vez de aceptar el
cambio y no aplicarlo.

#### 2.5.1. Añadido en la v2: efectos sobre la salida

La interfaz se organiza en cinco pestañas —Videos, Micrófono, Detector, Efectos
y Salida— porque el panel único obligaba a desplazarse y con los efectos no
cabría.

La pestaña **Efectos** añade, todo ajustable en vivo y sin volver a preparar la
caché: desenfoque, pixelado, brillo, contraste, saturación, zoom, grano, viñeta
y espejo. Los valores son **porcentajes derivados del ancho del fotograma**, no
píxeles absolutos, de modo que el mismo ajuste se ve igual en cualquier
resolución. Hay preajustes porque nueve deslizadores a cero no sugieren qué
hacer con ellos.

Las resoluciones ofrecidas pasan de cuatro a nueve (320x240 a 1920x1080) y las
cadencias a siete (10 a 30 FPS).

Lo instructivo de esta función fue el rendimiento: la primera implementación
consumía **76 % de un núcleo**, cuatro veces el objetivo, **sin que los FPS se
movieran** —el reloj absorbía los 28 ms por fotograma y la salida se veía
perfecta—. Tres optimizaciones lo bajaron a **13,8 %**, y la más instructiva fue
limitar OpenCV a un hilo: a 640x480 su paralelización no mejora el tiempo de
pared y triplica la CPU consumida. Detalle en `docs/ARQUITECTURA.md` §4.5 y
`docs/METRICS.md` §Medición 3.

#### 2.5.2. Añadido en la v2: grabador guiado y segundo plano

**Grabador con temporizador** (pestaña Grabar). El plan daba las condiciones de
grabación como recomendaciones en un documento. Ahora el programa las impone:
guías de encuadre sobre la vista previa, cuenta atrás, señales del guion en
pantalla con sus tiempos, y parada automática. El guion es datos
(`RecordingPlan`), así que se verifica entero sin cámara.

Dos fallos que solo aparecieron al probarlo con una webcam real:

* **La cadencia declarada miente.** Una cámara que anuncia 30 FPS entregaba 10
  con poca luz. Escribiendo 30 en la cabecera, un clip de 5 s quedaba de 1,7 s y
  se reproducía al triple de velocidad. Ahora se mide durante la cuenta atrás.
* **Nuestra propia cámara virtual se ofrecía como fuente**, lo que habría creado
  un bucle. Se detecta ahora por comportamiento: dos fotogramas separados
  250 ms que sean idénticos bit a bit delatan una imagen de relleno congelada,
  porque un sensor real nunca repite un fotograma.

**Segundo plano** (casilla en Salida). Al cerrar la ventana la aplicación sigue
en el área de notificación, con un icono que cambia de color según el estado del
detector. Es minimización a bandeja estándar, no evasión: el proceso sigue
visible en el Administrador de tareas y el nombre de la cámara sigue en la lista
del programa de videollamada. La documentación lo dice explícitamente para no
dar una falsa sensación de invisibilidad.

La aplicación no necesita cuentas, autenticación, backend, base de datos ni
servicios en la nube.

---

## 3. Arquitectura técnica

Python 3.11. Dependencias finales, todas con rueda precompilada para Windows
x64, sin compilador necesario:

| Componente | Elección | Por qué esta y no otra |
|---|---|---|
| Captura de audio | `sounddevice` 0.5.1 | API de callback más limpia que PyAudio y PortAudio incluido en la rueda |
| Detección de voz | `webrtcvad-wheels` 2.0.14 | **El paquete `webrtcvad` normal no trae rueda para Windows y exige Visual C++ Build Tools.** `webrtcvad-wheels` es el mismo código con rueda precompilada |
| Video | `opencv-python-headless` 4.10 | **La variante `headless` es obligatoria:** la normal trae sus propios plugins de Qt que chocan con PySide6 |
| Composición | NumPy + OpenCV | Un `addWeighted` por fotograma de transición |
| Interfaz | `PySide6-Essentials` 6.8.1 | Solo QtCore/QtGui/QtWidgets; el paquete completo añade ~200 MB al ejecutable sin aportar nada |
| Cámara virtual | `pyvirtualcam` 0.15 | Escribe al filtro de OBS sin OBS abierto |
| Métricas | `psutil` | CPU y memoria del proceso |
| Distribución | PyInstaller | Ejecutable único |

No se usa FFmpeg como proceso externo: OpenCV ya trae su decodificador y un
proceso hijo añadiría complejidad de empaquetado y de gestión del ciclo de vida.

### 3.1. Decisión central de la v2: caché de fotogramas pre-decodificados

El plan pedía "evitar decodificar repetidamente los videos desde cero" sin
concretar cómo. Es la decisión que determina el rendimiento, así que va aquí
explícita.

**Problema.** Decodificar H.264 en el bucle de salida no solo cuesta CPU: cuesta
de forma *irregular*. Un fotograma clave cuesta un orden de magnitud más que un
fotograma intermedio, lo que produce picos y jitter justo cada vez que el bucle
vuelve al principio.

**Solución.** Cada clip se convierte **una vez** a un archivo de fotogramas en
crudo, ya en la resolución, los FPS, el encuadre, el espejo y el formato de
píxel de salida. En ejecución, obtener un fotograma es indexar un `np.memmap`:
una copia de memoria, coste constante, sin decodificador en el bucle.

**Formato interno NV12.** La cámara virtual de OBS es NV12 de forma nativa (se
verificó: `native_fmt = NV12`). Guardando la caché ya en NV12, la cadena completa
queda sin conversiones de color: el fotograma sale del `memmap` y entra en la
cámara tal cual. Y el fundido cruzado **sigue siendo correcto sobre los bytes
crudos**, porque YUV es una transformación afín de RGB y por tanto interpolar
linealmente en uno u otro espacio da el mismo resultado:

```
  α·(M·RGB₁ + c) + (1−α)·(M·RGB₂ + c) = M·(α·RGB₁ + (1−α)·RGB₂) + c
```

Hay una prueba automatizada que comprueba esta equivalencia numéricamente.
NV12 además ocupa la mitad que BGR (460.800 frente a 921.600 bytes por
fotograma a 640 × 480).

**Coste.** 6,9 MB por segundo de clip a 640 × 480 y 15 FPS; dos clips de 8 s son
111 MB en disco, medidos. Es el precio explícito de no decodificar en el bucle.
El sistema operativo mantiene en RAM solo las páginas en uso; existe además un
modo `--ram` que carga todo a memoria y un tope configurable por clip.

La caché se identifica por un hash de la ruta, el tamaño y la fecha del archivo
original más todos los parámetros que la afectan, así que cambiar cualquier cosa
—o sustituir el video por otro con el mismo nombre— la invalida sola.

### 3.2. Modelo de hilos

Exactamente tres hilos, que es lo que concreta el "no crear hilos sin control"
del plan:

```
  Hilo de UI (Qt)          lee estado publicado, repinta a 10 FPS
                             ▲
                             │ instantáneas inmutables bajo cerrojo
  Hilo de audio ────────────►│   captura → remuestreo → VAD → histéresis
                             │
  Hilo de render ───────────►│   reloj → compositor → cámara virtual
```

Más el hilo de callback de PortAudio, que solo hace mezcla a mono y una copia a
una cola acotada: ningún pico de CPU en otro sitio puede cortar la captura.

La comunicación entre hilos es siempre en un sentido y mediante instantáneas
inmutables (`dataclass(frozen=True)`) protegidas por un cerrojo. Ningún hilo
espera a otro. El arrastre de un deslizador de la UI no puede provocar un tirón
en la salida.

### 3.3. Reloj con corrección de deriva

El objetivo de cada fotograma se calcula por acumulación (`next += 1/fps`), no
durmiendo un delta cada vuelta. Así el error de cada espera no se suma: en 30
minutos la cadencia media se mantiene exacta. Un retraso de más de tres periodos
reancla el reloj y se cuenta como resincronización, en lugar de intentar
recuperar emitiendo una ráfaga.

### 3.4. Estructura del código

```
camvoice/
  config.py            AppConfig y secciones, validación, persistencia JSON
  controller.py        Session: ata audio, clips, render y métricas
  metrics.py           muestreo de CPU/memoria/FPS, CSV y resumen
  cli.py               doctor, devices, probe, prepare, run, bench, vad-test
  testclips.py         generador de clips sintéticos de prueba
  audio/
    capture.py         sounddevice, negociación de formato, cola acotada
    resample.py        FIR paso bajo + interpolación con estado
    vad.py             EnergyDetector y WebrtcDetector
    gate.py            SpeechGate: histéresis temporal (puro, determinista)
    pipeline.py        hilo de audio y recuperación de dispositivo
  video/
    cache.py           preparación y acceso memmap, geometría, NV12
    clip.py            posición de reproducción, bucle pingpong
    composer.py        máquina de estados visual (pura, determinista)
    engine.py          hilo de render, reloj, vista previa
  output/
    sink.py            cámara virtual con reapertura, sumidero nulo
  ui/app.py            ventana PySide6
```

Las dos piezas con la lógica de comportamiento —`gate.py` y `composer.py`— son
deterministas y no tocan hardware ni reloj. De ahí que todo el comportamiento
visual y de detección se pueda verificar sin micrófono ni cámara.

---

## 4. Requisitos de rendimiento

Equipo objetivo:

- Procesador Intel Core i5 o equivalente.
- 16 GB de RAM recomendados; compatibilidad deseada con 8 GB.
- Gráficos integrados Intel o AMD.
- Windows 10 u 11 de 64 bits.

Objetivos:

| Métrica | Objetivo |
|---|---|
| Resolución | 640 × 480 |
| Cadencia | 15 FPS |
| Latencia de detección | 200-400 ms |
| CPU | < 25 % en el equipo de referencia, en prueba sostenida |
| Memoria | sin crecimiento continuo (< 1 MB/min de deriva) |
| Dependencias | sin CUDA, GPU dedicada ni aceleradores |

**Añadido en la v2 — cómo se mide, porque "medir el CPU" admite respuestas que
no se pueden comparar entre equipos.** El subcomando `camvoice bench` produce un
CSV y un resumen con:

- CPU en **dos escalas**: la del Administrador de tareas (puede pasar de 100 %
  con varios núcleos) y **normalizada por número de núcleos lógicos**, que es la
  comparable entre equipos. El objetivo del 25 % se evalúa sobre la normalizada.
- **Percentiles, no solo medias**: lo que rompe una videollamada es el peor caso.
- **Pendiente de memoria por mínimos cuadrados** en MB/min, que detecta una fuga
  lenta que una comparación inicio/fin no vería.
- **FPS efectivos** (fotogramas emitidos por segundo de reloj, lo que ve la
  aplicación receptora) separados de la **capacidad** del motor.
- Identificación del equipo (CPU, núcleos, RAM, SO) en el propio informe.

El informe emite un veredicto explícito por cada objetivo. Las cifras obtenidas
se registran en `docs/METRICS.md` junto al equipo donde se tomaron.

---

## 5. Limitaciones conocidas

Esta solución es una primera aproximación basada en clips pregrabados.

**No genera sincronización labial real.** El video de habla se reproduce cuando
el detector identifica voz, independientemente del contenido, la duración de las
palabras o la posición de los labios. El resultado presentará diferencias entre
el audio real y el movimiento de la boca: es inherente al enfoque, no un defecto
a corregir.

La transición entre clips tampoco garantiza continuidad perfecta en expresiones,
parpadeos, respiración o postura.

**Añadido en la v2 — límites concretos que conviene saber de antemano:**

- **La latencia de activación es visible.** Con `attack_ms` de 120 ms, el video
  empieza a hablar ~120 ms después de que empiece la voz, más el fundido. Es
  perceptible si alguien mira la boca con atención. Bajarlo aumenta los falsos
  positivos: es un compromiso, no un parámetro que tenga un valor óptimo.
- **El primer fonema nunca está sincronizado**, por construcción.
- **Un silencio largo mostrando el clip neutral en bucle delata el truco** más
  que el desajuste labial. Ahí es donde importan la duración del clip y el modo
  de ida y vuelta.
- **Risas, toses y carraspeos activan el estado de habla.** Para el detector son
  voz.
- **Es detectable.** Quien se fije verá que la boca no corresponde a las
  palabras. Esta aplicación no pretende lo contrario.

No incorpora generación facial, animación neuronal, modelos de difusión ni
inferencia generativa.

---

## 6. Criterios de aceptación

> Los criterios de la v1 se conservan íntegros. En los que no eran comprobables
> tal como estaban redactados se ha añadido **cómo se verifica**, con el comando
> concreto o la prueba automatizada correspondiente. Ningún criterio se declara
> cumplido sin haber sido ejecutado.

### CA-01. Ejecución local
Sin envío de audio ni video a servidores externos; funciona sin conexión a
Internet una vez instalada.
**Verificación:** inspección de dependencias (ninguna abre sockets) y ejecución
con el adaptador de red desactivado.

### CA-02. Ausencia de LLMs
Ni el código ni las dependencias de ejecución requieren LLMs, servicios de IA
generativa, claves API ni suscripciones.
**Verificación:** `docs/LICENCIAS.md` enumera las siete dependencias de
ejecución, su licencia y su función.

### CA-03. Selección de archivos
El usuario selecciona los dos videos desde la interfaz y recibe un mensaje
comprensible si son incompatibles o no se pueden abrir.
**Verificación:** `tests/test_cache.py` cubre archivo inexistente, archivo que no
es video y archivo con códec no legible; la UI valida al seleccionar, antes de
procesar.

### CA-04. Detección de voz
Al hablar cerca del micrófono el sistema pasa a SPEAKING dentro del objetivo de
latencia; al callar vuelve a SILENT tras el tiempo de permanencia configurado.
**Verificación:** `tests/test_gate.py` comprueba que la latencia medida coincide
exactamente con `attack_ms` y la de cierre con `release_ms`, en ventanas de 10,
20 y 30 ms. Con micrófono real: `camvoice vad-test --duration 30`, que imprime la
cronología de cambios con marcas de tiempo.

### CA-05. Estabilidad del detector
El ruido de fondo moderado no provoca alternancias continuas. La sensibilidad es
ajustable.
**Verificación:** dos pruebas automatizadas — 30 s de positivos espaciados no
producen ninguna activación, y 60 s de positivos aleatorios al 30 % producen un
máximo de 4 cambios. Con micrófono real, `vad-test` informa la tasa de cambios
por minuto y avisa por encima de 30.

### CA-06. Reproducción de clips
Alterna entre los clips sin detener el proceso, sin reinicios innecesarios y sin
pantallas negras.
**Verificación:** `tests/test_composer.py` comprueba que ningún fotograma queda
fuera del rango de valores de los dos clips —lo que detectaría un negro—, que el
fundido es monótono, y que una transición invertida a mitad no salta.

### CA-07. Salida virtual
La salida aparece como cámara seleccionable en una aplicación compatible, **sin
necesidad de instalar OBS**.
**Verificación:** `camvoice-cli install-camera` registra la cámara propia,
`camvoice-cli cameras` la lista en 64 y 32 bits y `camvoice-cli doctor` la abre
e informa del dispositivo, su formato nativo y el backend usado. Verificado sin
OBS instalado en la ruta. **Requiere además una comprobación manual** en una
aplicación receptora real.

### CA-08. Continuidad de imagen
En una prueba de 30 minutos la cámara mantiene salida continua, sin bloqueos ni
degradación progresiva.
**Verificación:** `camvoice bench --duration 1800 --metrics`, con veredicto
automático sobre FPS sostenidos y pendiente de memoria.

### CA-09. Rendimiento
Probada en un equipo con gráficos integrados y procesador Core i5 o equivalente,
registrando FPS efectivos, CPU, memoria y latencia.
**Verificación:** `camvoice bench` genera el informe y el CSV; las cifras van a
`docs/METRICS.md` con la identificación del equipo.

### CA-10. Resolución y FPS
Salida predeterminada 640 × 480 a 15 FPS, ajustable si el hardware lo permite.
**Verificación:** valor por defecto en la configuración; cuatro resoluciones
preconfiguradas en la UI y cualquier valor par por configuración.

### CA-11. Interfaz
El usuario inicia y detiene la cámara, cambia el micrófono, modifica la
sensibilidad y observa el estado del detector sin reiniciar la aplicación.
**Verificación:** manual, con la tabla de [§2.5](#25-interfaz-de-usuario) como
referencia de qué es ajustable en caliente.

### CA-12. Recuperación de errores
Si el micrófono se desconecta o un video deja de estar disponible, la aplicación
informa y permite recuperarse sin cerrarse.
**Verificación:** `tests/test_engine.py` simula el fallo de la cámara virtual y
comprueba que el motor sigue vivo y publica el error. El hilo de audio reintenta
con espera creciente (0,5 → 4 s) y fuerza SILENT mientras está caído. Una caché
ilegible congela la última imagen buena en lugar de emitir negro. **Falta la
prueba manual de desconexión física.**

### CA-13. Distribución
Ejecutable o instalador para Windows, con instrucciones de instalación,
configuración y desinstalación.
**Verificación:** `build/camvoice.spec` y `docs/INSTALACION.md`. **Pendiente de
generar y probar en una máquina limpia.**

### CA-14. Documentación
Arquitectura, dependencias, licencias, limitaciones de sincronización y
procedimiento de prueba de la cámara virtual.
**Verificación:** `docs/ARQUITECTURA.md`, `docs/LICENCIAS.md`,
`docs/PRUEBAS.md`, `docs/METRICS.md` y este plan.

---

## 7. Plan de implementación

**Fase 1: Prueba de concepto** — completada
- Leer dos videos locales, capturar audio, detectar voz y silencio, alternar
  clips, mostrar el resultado.

**Fase 2: Mejora de estabilidad** — completada
- Histéresis, tiempos de permanencia, guarda de ciclo de trabajo, transiciones
  con suavizado, bucle sin costura, instrumentación de CPU y memoria.

**Fase 3: Cámara virtual** — completada en lo automatizable
- Integración con la cámara virtual de OBS sin OBS abierto, verificada en este
  equipo. Falta la validación manual en una aplicación receptora.

**Fase 4: Interfaz y distribución** — interfaz completada, distribución pendiente
- Interfaz de configuración y gestión de errores, hechas. Generar el ejecutable
  y ejecutar las pruebas de aceptación manuales, pendiente.

---

## 8. Estado de la implementación

| Criterio | Estado | Qué falta |
|---|---|---|
| CA-01 Ejecución local | Verificado por inspección | Prueba sin red |
| CA-02 Sin LLMs | Verificado | — |
| CA-03 Selección de archivos | Verificado (automatizado) | — |
| CA-04 Detección de voz | Verificado (automatizado) | Confirmación con voz real |
| CA-05 Estabilidad | Verificado (automatizado) | Confirmación en entorno ruidoso real |
| CA-06 Reproducción | Verificado (automatizado) | Revisión visual con clips reales |
| CA-07 Salida virtual | Cámara propia registrada y alimentada, sin OBS | Prueba manual en aplicación receptora (Teams/Zoom/Chrome) |
| CA-08 Continuidad 30 min | Instrumentado | Ejecutar la prueba de 30 min |
| CA-09 Rendimiento | **Medido en un Core i5-10300H con Intel UHD integrada**, que es el perfil de referencia | Repetir con duración de 30 min |
| CA-10 Resolución y FPS | Verificado: 9 resoluciones y 7 cadencias | — |
| CA-11 Interfaz | Implementado | Recorrido manual |
| CA-12 Recuperación | Verificado (automatizado) | Desconexión física del micrófono |
| CA-13 Distribución | Especificado | Generar y probar el ejecutable |
| CA-14 Documentación | Completa | — |

---

## 9. Cambios respecto a la v1

Lo que se añadió, y el problema concreto que evita:

1. **Caché de fotogramas pre-decodificados en NV12** ([§3.1](#31-decisión-central-de-la-v2-caché-de-fotogramas-pre-decodificados)).
   El plan pedía no decodificar repetidamente sin decir cómo. Esto elimina el
   decodificador del bucle y, al coincidir con el formato nativo de la cámara,
   también todas las conversiones de color.

2. **Guarda de ciclo de trabajo en la histéresis**
   ([§2.2.1](#221-añadido-en-la-v2-la-guarda-de-ciclo-de-trabajo)). La histéresis
   y la tolerancia a pausas del plan, implementadas literalmente, se
   contradicen: el ruido de fondo abre la puerta por acumulación. Se detectó
   escribiendo la prueba de CA-05, no en uso.

3. **`webrtcvad-wheels` en lugar de `webrtcvad`** ([§3](#3-arquitectura-técnica)).
   El paquete que el plan sugiere no tiene rueda para Windows y exige Visual C++
   Build Tools: habría bloqueado la instalación.

4. **`opencv-python-headless` en lugar de `opencv-python`**
   ([§3](#3-arquitectura-técnica)). La variante normal incluye plugins de Qt que
   chocan con PySide6. El síntoma es un fallo al arrancar difícil de atribuir.

5. **Los tres límites de la cámara virtual de OBS**
   ([§2.4.1](#241-añadido-en-la-v2-tres-límites-del-mecanismo-elegido)). Que sea
   DirectShow, de productor único y enumerada al arrancar produce tres síntomas
   que parecen fallos de la aplicación.

6. **Separación entre lo ajustable en caliente y lo que no**
   ([§2.5](#25-interfaz-de-usuario)). El plan daba a entender que todo era
   ajustable en marcha.

7. **Bucle de ida y vuelta, y política de rebobinado**
   ([§2.3](#23-reproducción-del-video)). Resuelve la costura del bucle y el
   requisito contradictorio de "recordar la posición" pero "no dejar la boca
   abierta".

8. **Aclaración de que el audio no se enruta**
   ([§1.1](#11-lo-que-esta-aplicación-no-hace-con-el-audio)). La Fase 3 de la v1
   pedía "verificar continuidad de audio y video", lo que sugiere que la
   aplicación maneja audio. No lo hace.

9. **Criterios de aceptación con método de verificación**
   ([§6](#6-criterios-de-aceptación)). Varios criterios de la v1 no eran
   comprobables tal como estaban redactados.

10. **Metodología de medición de CPU** ([§4](#4-requisitos-de-rendimiento)). "CPU
    inferior al 25 %" no se puede comparar entre equipos sin normalizar por
    núcleos ni distinguir media de percentil.

11. **Recomendaciones de grabación** ([§2.1](#21-configuración-inicial)). La
    calidad depende más de los clips que del código, y el software no puede
    suplirlo.

14. **Grabador guiado y segundo plano**
    ([§2.5.2](#252-añadido-en-la-v2-grabador-guiado-y-segundo-plano)). El plan
    dejaba las condiciones de grabación como recomendaciones escritas; ahora el
    programa las impone con temporizador y señales en pantalla. Más el guion
    fonético en `docs/GUION-GRABACION.md`.

13. **Cadena de efectos y UI por pestañas**
    ([§2.5.1](#251-añadido-en-la-v2-efectos-sobre-la-salida)). La v1 no
    contemplaba efectos. Se añadieron nueve, ajustables en vivo y con
    parámetros independientes de la resolución, más preajustes. El coste tuvo
    que optimizarse 5,5 veces para caber en el objetivo de CPU.

12. **Cámara virtual independiente de OBS** ([§2.4](#24-cámara-virtual)). La v1
    proponía OBS Virtual Camera «como primera implementación» y la aplicación
    quedaba atada a instalar OBS. Ahora incluye su propio filtro MIT y lo
    registra ella misma, apareciendo como «CamVoice Virtual Camera». El detalle
    que lo hizo barato: el nombre de la cámara es un parámetro de `regsvr32`, no
    algo que haya que compilar. Análisis completo en `docs/CAMARA-VIRTUAL.md`.
