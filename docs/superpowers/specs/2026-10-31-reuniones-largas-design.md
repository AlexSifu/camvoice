# Diseño: CamVoice para reuniones largas de tres personas

Fecha: 2026-10-31 · Estado: borrador para aprobación

## 1. Intención y porqué

El usuario usa CamVoice en reuniones de **1 a 2 horas con otras dos personas**.
Con el diseño actual (dos clips, fundido de 160 ms, sin relación con la voz)
pasaron tres cosas en una reunión real:

1. Los otros participantes pidieron **saludar** y **sonreír** para comprobar que
   estaba presente. El video no podía hacerlo.
2. Se nota el **cambio entre hablar y callar**: salto de pose, cara "fantasma"
   en el fundido y cambio de luz o fondo.
3. **La boca no sigue lo que dice**: se mueve en las pausas, sigue moviéndose
   después de callar y siempre arranca con el mismo gesto.

El usuario habla de dos formas: **intervenciones sueltas y cortas** y también
**tramos largos seguidos**. Pasa buena parte de la reunión escuchando. Puede
usar teclado y ratón con normalidad durante la reunión. Pide que el resultado
"se vea fino", que quede completo y sin problemas.

**Nota de contexto (dicha al usuario):** las peticiones de gestos eran
comprobaciones de presencia. Ningún sistema con video pregrabado aguanta una
comprobación abierta ("toca tu oreja izquierda"). El diseño cubre los gestos
**previstos y grabados**, no los arbitrarios.

## 2. Objetivos

- **O1. Gestos a demanda.** Saludar, sonreír, asentir, mirar arriba y reírse
  (lista ampliable por el usuario). Se lanzan con un atajo de teclado global o
  con un clic, y empiezan a verse en ≤ 300 ms.
- **O2. Transiciones invisibles.** Entre silencio, habla y gestos no hay salto
  de pose, ni doble exposición visible, ni cambio de luz o fondo.
- **O3. Boca coherente con la voz.** La boca se abre con la energía de la voz y
  se cierra en las pausas, incluidas las pausas cortas dentro de una frase.
- **O4. Aguantar 2 horas.** No se percibe un patrón repetido en el silencio ni
  en el habla. Memoria y CPU estables. Sin congelados ni fotogramas negros.
- **O5. Grabación guiada** de todo el material nuevo desde la propia
  aplicación, con superposición de la pose de referencia para alinear.

## 3. No objetivos

- Lectura de labios fonética exacta (visemas por fonema).
- Gestos no grabados o generados.
- Distinguir tu voz de la de los otros participantes. Se exige **auriculares**
  y se documenta; no se resuelve en software.
- Reconocer órdenes por voz ("sonríe").
- Cualquier cambio en la instalación o el registro de la cámara virtual.

## 4. Anti-objetivos (sería un fracaso aunque "funcione")

- Que un gesto tarde más de medio segundo en arrancar o quede cortado a mitad.
- Que la boca se mueva con los ojos cerrados a mitad de parpadeo congelado o
  que aparezcan dos caras superpuestas durante más de 1 fotograma perceptible.
- Usar ganchos globales de teclado de bajo nivel (`SetWindowsHookEx`): los
  antivirus los tratan como keylogger.
- Superar de media el **25 % de un núcleo** o **20 ms por fotograma** a
  640x480 con efectos activos.
- Romper configuraciones existentes: un `config` con solo `speaking_clip` y
  `silent_clip` debe seguir arrancando sin que el usuario toque nada.

## 5. Restricciones (heredadas, sin cambios)

Todo local; sin IA generativa ni LLM; sin entrenar modelos; sin GPU; sin
internet; Windows x64; Python 3.12; dependencias actuales (`numpy`,
`opencv-python-headless`, `PySide6-Essentials`, `sounddevice`, `webrtcvad`).
**No se añaden dependencias nuevas.** Se pueden usar el detector Haar de caras
incluido en OpenCV (`cv2.data.haarcascades`) y el flujo óptico DIS
(`cv2.DISOpticalFlow`), que son visión clásica y no generativos.

Modelo de hilos: se mantienen los tres hilos propios (UI, audio, render). Los
atajos se reciben en el hilo de UI. No hay hilos nuevos de forma permanente;
el análisis de preparación corre en el `PrepareWorker` existente.

## 6. Diseño

### 6.1 Material grabado (perfil)

| Pool | Contenido | Duración recomendada |
|---|---|---|
| **Silencio** | 1 a 3 clips escuchando, parpadeando, respirando | 60 s en total (mín. 20 s; por debajo de 40 s se avisa de que el bucle puede notarse en reuniones largas) |
| **Habla** | 1 a 2 clips leyendo texto anodino a ritmo normal | 60 s en total (mín. 15 s) |
| **Gestos** | Un clip por gesto: 1 s neutro → gesto → vuelta → 1 s neutro | 3-5 s cada uno |

Gestos por defecto (nombre, atajo): saludar `Ctrl+Alt+F1`, sonreír `Ctrl+Alt+F2`,
asentir `Ctrl+Alt+F3`, reírse `Ctrl+Alt+F4`, mirar arriba `Ctrl+Alt+F5`,
negar `Ctrl+Alt+F6`. `Ctrl+Alt+F12` cancela el gesto en curso. Se evitan
`Ctrl+Alt+número` porque en teclado español Ctrl+Alt equivale a AltGr y
bloquearía escribir `|`, `@` y `#` en todas las aplicaciones. La interfaz
rechaza atajos Ctrl+Alt+tecla imprimible por el mismo motivo.

Configuración (`config.py`):

- `VideoConfig.silent_clips: list[str]` y `speaking_clips: list[str]`. Los campos
  actuales `silent_clip` y `speaking_clip` se **migran al cargar**: si las
  listas están vacías y el campo antiguo no, se usa como único elemento. Al
  guardar se escriben las listas; los campos antiguos se dejan por
  compatibilidad con el primer elemento.
- Nueva sección `GesturesConfig` con `items: list[GestureItem(name, clip,
  hotkey, enabled)]` y `auto_nod: AutoNodConfig` (ver §6.7).
- `VideoConfig.reference`: implícito, el **primer clip de silencio** es la
  referencia de alineación y color de todo el perfil.

### 6.2 Preparación: normalización entre clips

Al preparar la caché, cada clip que no sea la referencia se **normaliza contra
ella**:

1. **Alineación geométrica.** Se calcula el fotograma mediano de cada clip
   (luma reducida) y se estima una transformación euclídea (traslación +
   rotación pequeña) con `cv2.findTransformECC` contra la mediana de la
   referencia. Se aplica la misma transformación a todos los fotogramas del clip
   durante la transcodificación (coste una vez, no por fotograma en vivo). Si
   ECC no converge o el desplazamiento supera el 8 % del ancho, no se alinea y
   se muestra un aviso ("este clip se grabó con la cámara en otra posición").
2. **Igualación de color.** Se igualan media y desviación por canal en Lab de la
   mediana del clip a las de la referencia, aplicado como LUT por canal.

La clave de caché (`cache_key`) incluye la identidad (ruta, tamaño, mtime) de la
referencia y una versión de normalización, para que cambiar la referencia
invalide las cachés dependientes. `ClipMeta` guarda la transformación aplicada y
la versión.

### 6.3 Preparación: índice de movimiento

Tras preparar todos los clips del perfil se construye un **índice** (archivo
`.npz` junto a la caché, con clave derivada de las claves de todos los clips):

- **Descriptor por fotograma:** luma reducida a 32x24, normalizada (media 0,
  norma 1). Una región ampliada alrededor de la cara pesa el doble.
- **Vecinos:** para cada fotograma de silencio y de habla, los k = 24 fotogramas
  más parecidos del conjunto silencio+habla (excluyendo ±5 fotogramas propios),
  con su distancia. Se guarda además el **percentil 90** de distancias entre
  fotogramas consecutivos del mismo clip como **umbral de salto invisible**
  `d_ok`: un salto a esa distancia no se distingue de un fotograma normal.
- **Apertura de boca por fotograma** (0..1) en los clips de habla y silencio:
  1. Caja de cara con Haar sobre ~10 fotogramas repartidos; se toma la mediana.
     Si no se detecta cara, se usa la caja estimada por las guías de encuadre
     (ojos a un tercio de altura, cabeza centrada).
  2. Región de boca: tercio inferior de la caja, 50 % central en ancho.
  3. Métrica: proporción de píxeles oscuros (por debajo del percentil 20 de la
     región en el clip de silencio de referencia), suavizada con media de 3.
  4. Normalización por percentiles 5 y 95 del pool de habla → 0..1.
  Se marca como **"boca cerrada"** todo fotograma con apertura ≤ 0,15.
- **Gestos:** no entran en los vecinos. Se guarda el descriptor de su primer y
  último fotograma.

Coste esperado: < 3 s para 2.000 fotogramas. Memoria del índice < 20 MB.

### 6.4 Audio: objetivo de apertura de boca

`AudioPipeline` publica en `VoiceStatus` un nuevo campo `mouth_target: float`
(0..1), calculado por trama:

- `nivel = clamp((rms_db − noise_floor_db − snr_on_db) / rango, 0, 1)`, con
  `rango` = 18 dB por defecto.
- Envolvente con subida rápida (τ = 25 ms) y bajada media (τ = 90 ms).
- Si la trama no es voz (`voiced = False`), el objetivo cae a 0 con la constante
  de bajada: así **la boca se cierra en las pausas** aunque la puerta siga en
  SPEAKING.

La puerta (`SpeechGate`) sigue decidiendo **qué pool** se usa. Como la boca ya
se cierra sola en pausas, se sube `release_ms` por defecto a 600 ms para no
alternar entre pools en cada respiración. Se añade un preajuste
**"Conversación"** con estos valores.

La interfaz del motor cambia de `voice_state() -> VoiceState` a
`voice_input() -> VoiceInput(state, mouth_target)`.

### 6.5 Director (sustituye a `FrameComposer`)

Módulo nuevo `video/director.py`, **puro y determinista** igual que el
compositor actual: recibe `(VoiceInput, órdenes de gesto)` y devuelve un
fotograma y un `ComposeInfo` ampliado. RNG con semilla inyectable para los
tests.

Estados: `SILENT`, `SPEAKING`, `GESTURE`, y `TRANSITION(desde, hacia)`.

**Reproducción dentro de un pool ("video textures").** En cada fotograma, los
candidatos son el siguiente fotograma natural y sus vecinos con distancia ≤
`d_ok` (en SILENT) o ≤ `2·d_ok` (en SPEAKING, donde seguir la boca pesa más).
Se elige el de menor coste:

```
coste = w_boca  · |apertura(c) − objetivo|        (solo en SPEAKING)
      + w_salto · [c no es el siguiente natural]
      + w_reuso · uso_reciente(c)                   (ventana de 90 s)
      + w_dist  · distancia(actual, c)
```

- En SILENT el objetivo de boca es 0 y `w_boca` actúa solo como restricción
  (descarta fotogramas con apertura > 0,15). Los saltos se permiten como mucho
  cada 3-8 s (aleatorio) para que el silencio no "tiemble".
- En SPEAKING se puede saltar en cualquier fotograma si mejora la boca, con un
  mínimo de 2 fotogramas entre saltos.
- **Sin pingpong.** Al llegar al final de un clip, se salta al mejor vecino
  dentro del pool. `loop_mode` queda solo para el modo heredado.
- Todo salto con distancia > `d_ok / 2` se suaviza con un fundido de 2
  fotogramas. Si en SPEAKING ningún candidato acerca la boca al objetivo, se
  sigue el fotograma natural: **la invisibilidad (O2) manda sobre la boca (O3)**.

**Transiciones entre estados.**

| De → a | Fotograma de entrada | Duración |
|---|---|---|
| Silencio → habla | El fotograma de habla con boca cerrada más parecido al actual | Adaptativa |
| Habla → silencio | El fotograma de silencio más parecido al actual | Adaptativa |
| Cualquiera → gesto | Fotograma 0 del gesto | Adaptativa, máx. 200 ms |
| Gesto → silencio o habla | El más parecido al último fotograma del gesto, en el pool que indique la voz en ese momento | Adaptativa |

**Duración adaptativa:** `d ≤ d_ok` → 2 fotogramas; `d` grande → hasta 400 ms,
interpolando linealmente entre ambos extremos.

**Mezcla:** si `d ≤ 2·d_ok`, fundido con smoothstep (como ahora). Si es mayor,
**fundido con flujo óptico**: DIS en modo rápido sobre la luma reducida a la
mitad, deformando ambos fotogramas hacia el punto intermedio según el progreso,
y luego mezclando. Se aplica a los planos Y y UV de NV12 (UV con el flujo a
mitad de escala). Solo corre durante transiciones.

**Gestos.**

- Una orden de gesto entra en una cola de longitud 1. Una orden nueva mientras
  hay otra en cola la sustituye. Una pulsación repetida del gesto en curso se
  ignora.
- El gesto se reproduce **una vez, hacia delante, a velocidad 1**, y tiene
  prioridad sobre la voz: durante un gesto la boca no sigue a la voz.
- Al terminar se vuelve al pool que indique la voz en ese momento.
- `Cancelar` adelanta la salida: transición inmediata desde el fotograma
  actual del gesto.
- Durante una transición hacia otro estado, una orden de gesto redirige la
  transición hacia el fotograma 0 del gesto, conservando la mezcla en curso.

**Configuraciones antiguas.** Hay **un solo camino de reproducción**. Con un
único clip de silencio y uno de habla y sin gestos, el director usa el mismo
algoritmo con pools de un clip. `FrameComposer` y `ClipPlayer` se eliminan y
sus tests se reescriben contra el director. Campos existentes:

| Campo | Destino |
|---|---|
| `speaking_speed`, `silent_speed` | Se mantienen: velocidad de reproducción de cada pool |
| `transition_ms` | Pasa a ser la duración **máxima** de la transición adaptativa (por defecto 400) |
| `loop_mode`, `resume_window_ms` | Se siguen leyendo sin error pero se ignoran; la interfaz deja de mostrarlos |
| `release_ms` | El valor por defecto pasa a 600 solo para configuraciones nuevas; las guardadas conservan el suyo |

**Perfil** = el conjunto de clips de la configuración actual. No hay perfiles
con nombre.

**Cambios de material.** Añadir o quitar un clip (también tras grabarlo)
requiere volver a preparar. Si el motor está en marcha, se prepara en
`PrepareWorker` mientras sigue emitiendo con el material anterior, y al terminar
se reinicia el motor automáticamente. No hay intercambio de pools en caliente.
Se espera que la grabación se haga antes de la reunión.

### 6.6 Atajos y panel de gestos

- **Atajos globales** con `RegisterHotKey` de Win32 asociados al HWND de la
  ventana principal, que existe aunque esté oculta en la bandeja. Se reciben
  con un `QAbstractNativeEventFilter` (`WM_HOTKEY`) en el hilo de UI. Funcionan
  con la aplicación de videollamada en primer plano. No se usan ganchos de bajo
  nivel.
- Si un atajo no se puede registrar porque otra aplicación lo usa, la pestaña
  Gestos lo marca en rojo con el motivo y deja elegir otro.
- **Ratón:** submenú "Gestos" en el icono de bandeja y un **panel flotante**
  opcional: pequeño, siempre encima, sin marco, arrastrable, con un botón por
  gesto y uno de cancelar. Posición y visibilidad se guardan en la
  configuración.
- El hilo de UI envía la orden al motor mediante un método seguro entre hilos
  (`RenderEngine.request_gesture(name)` y `cancel_gesture()`), con una cola
  protegida por el cerrojo, que el hilo de render consume en el siguiente
  `_tick`.

### 6.7 Asentimiento automático (opcional, desactivado por defecto)

Si se activa, mientras la voz esté en SILENT durante más de 20 s se lanza el
gesto marcado como "automático" (por defecto *asentir*) cada 60-150 s
(aleatorio). Nunca mientras hablas ni en los 5 s siguientes a hablar.
Desactivado por defecto porque asentir cuando nadie habla también delata.

### 6.8 Grabación guiada

Se amplía la pestaña **Grabar** con tres tipos:

- **Silencio largo** (30 s por toma, se recomiendan 2): mismas señales que hoy.
- **Habla larga** (30 s por toma, se recomiendan 2): texto de lectura nuevo de
  unas 150 palabras, anodino, con mezcla de vocales y cierres labiales como el
  actual. Dos variantes de texto para que las tomas no sean iguales.
- **Gesto**: se elige el gesto de la lista. Señales: "Neutro, mira al objetivo"
  (1 s) → "Ahora: <gesto>" (2-3 s según gesto) → "Vuelve a la pose" (1 s) →
  "Neutro" (1 s).

En todos los tipos, cuando ya existe la referencia, se superpone su primer
fotograma al 30 % de opacidad (**pose fantasma**) para que el usuario coloque
la cabeza en el mismo sitio. Al terminar, la grabación se añade al pool o gesto
correspondiente.

`docs/GUION-GRABACION.md` se actualiza: qué grabar, en qué orden y cómo hacer
cada gesto para que empiece y termine en neutro.

### 6.9 Interfaz

- Pestaña **Videos**: listas de clips de silencio y de habla (añadir, quitar,
  reordenar), con la duración total de cada pool y el aviso si queda por debajo
  del mínimo.
- Pestaña nueva **Gestos**: tabla con nombre, clip, atajo (editable), estado del
  registro del atajo, botón "Grabar" y botón "Probar" (lo lanza en la vista
  previa). Casilla del panel flotante. Ajustes del asentimiento automático.
- Pestaña **Detector**: preajuste "Conversación". Aviso permanente y visible:
  "Usa auriculares: si el micrófono oye a los demás, tu video hablará con su
  voz".
- Barra de estado: estado actual (Silencio, Hablando, Gesto: saludar,
  Transición) y, en el modo de depuración, saltos por minuto y cobertura del
  pool.

### 6.10 Rendimiento y caché

- Caché esperada a 640x480 y 15 FPS: ~6,9 MB/s → perfil completo (60 + 60 + 6×4 s)
  ≈ 1 GB en disco, con acceso por mapeo de memoria como ahora. La preparación
  avisa si el disco no tiene espacio.
- `max_cache_mb` pasa a ser un tope **por perfil** (por defecto 3.072 MB).
- FPS por defecto: se mantiene 15. La interfaz recomienda 20 si la CPU lo
  permite. Todo el diseño funciona a cualquier FPS.

## 7. Criterios de aceptación

Verificables sin cámara ni micrófono, con clips sintéticos (`testclips` se
amplía con gestos y con un desplazamiento y cambio de color controlados):

1. **Transiciones:** en 1.000 cambios simulados silencio↔habla↔gesto, la
   diferencia media absoluta entre fotogramas de salida consecutivos nunca
   supera 2× el percentil 95 de la diferencia entre fotogramas consecutivos
   dentro de un mismo clip.
2. **Boca:** con una envolvente de voz sintética, la correlación entre
   `mouth_target` y la apertura del fotograma emitido (con 1 fotograma de
   desfase) es ≥ 0,7. En pausas de ≥ 150 ms, la apertura emitida baja de 0,2 en
   ≤ 3 fotogramas.
3. **Gestos:** desde la orden hasta el primer fotograma con peso ≥ 50 % del
   gesto pasan ≤ 300 ms. El gesto se emite completo. La cancelación vuelve en
   ≤ 400 ms.
4. **Repetición:** con un pool de silencio de 60 s, en 2 h simuladas se usa ≥ 95 % de los
   fotogramas del pool y ninguna secuencia de más de 4 s se repite idéntica
   antes de 10 min.
5. **Normalización:** un clip sintético desplazado 10 px y con +15 de brillo
   queda alineado a ≤ 1 px y con diferencia de media de luma ≤ 2 respecto a la
   referencia.
6. **Compatibilidad:** una configuración antigua de dos clips carga sin error,
   arranca sin que el usuario toque nada y alterna silencio y habla según la
   puerta de voz.
7. **Rendimiento:** `camvoice bench --duration 7200` con el perfil sintético
   completo: media ≤ 20 ms por fotograma, ≤ 25 % de un núcleo, memoria estable
   (memoria **privada** del proceso, sin contar las páginas del mapeo de la
   caché: crecimiento ≤ 50 MB entre el minuto 5 y el 120), cero fotogramas
   negros.
8. **Atajos:** el registro, el conflicto y la liberación al salir se prueban
   con un falso de la API Win32. El panel y el menú de bandeja tienen tests de
   UI como los existentes.

## 8. Lo que decidió el usuario

- Debe seguir todo local, sin IA generativa, sin GPU y sin internet.
- Los gestos se lanzan con teclado o ratón durante la reunión.
- Reuniones de 1 a 2 horas, tres personas, habla a ratos y también seguido.
- Quiere todo completo y pulido a nivel de código.

## 9. Decisiones mías (revisables)

- Gestos por defecto y atajos `Ctrl+Alt+F1..F6`, `Ctrl+Alt+F12` para cancelar.
- `d_ok` = percentil 90 de distancias consecutivas; SPEAKING admite saltos hasta
  `2·d_ok`; si hay conflicto, manda la invisibilidad sobre la boca.
- Un solo camino de reproducción; `loop_mode` y `resume_window_ms` se ignoran.
- Perfil = clips de la configuración actual; sin perfiles con nombre.
- Cambiar material re-prepara y reinicia el motor; sin cambio en caliente.
- La prueba de 2 h mide memoria privada, no el mapeo de la caché.
- El gesto tiene prioridad sobre la voz (la boca no sigue la voz durante un gesto).
- Asentimiento automático desactivado por defecto.
- Sin pingpong en el modo nuevo.
- `release_ms` por defecto a 600 ms y preajuste "Conversación".
- 15 FPS por defecto, recomendando 20.
- Medida de apertura de boca por visión clásica (Haar + píxeles oscuros), sin
  detectores de puntos faciales.
- Se elimina `FrameComposer` y su lógica pasa al director.

## 10. Lo que queda al criterio de quien lo construya

Pesos exactos de la función de coste, valores de los umbrales dentro de los
márgenes indicados, estructura interna de módulos y nombres, diseño visual del
panel flotante, redacción de los textos de grabación (respetando las reglas de
la §6.8), y el orden de implementación siempre que cada paso deje la aplicación
funcionando.
