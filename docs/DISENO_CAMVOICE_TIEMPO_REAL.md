# Diseño en tiempo real de CamVoice (modo director)

Estado: implementado. Complementa a [ARQUITECTURA.md](ARQUITECTURA.md), que
sigue describiendo la base (caché NV12, cámara virtual, hilos). Aquí se
explica lo añadido para reuniones de 1-2 horas: reproducción continua con
cambios en fotogramas ancla, micro-pausas, varios clips, gestos manuales
(con variantes para hablar), pausa manual del video y sesiones largas.

Restricciones que se mantienen: todo en local y sin conexión, sin IA
generativa, sin GPU obligatoria, sin servicios externos, sin grabar el
micrófono. Solo se usan dependencias ya presentes (NumPy, OpenCV, PySide6,
sounddevice, pyvirtualcam, webrtcvad).

## 1. Flujo

```
micrófono ─► VAD + histéresis (SpeechGate) ─► estado SILENT/SPEAKING ──┐
         └─► MicroPauseDetector (150-250 ms sin voz) ─► micro_pause ───┤   (hilo de audio)
                                                                        ▼
 atajos / botones / bandeja ─► peticiones (gesto, cancelar, pausa) ─► Director ─► efectos ─► cámara virtual
                                                                        ▲          (imagen limpia, sin rótulos)
             caché NV12 + índice y anclas de cada clip ─────────────────┘   (preparados offline / al crear el director)

 indicadores (pregrabado, pausa, estado, métricas) ─► solo ventana, bandeja y consola
```

- **Offline (al pulsar Preparar o `camvoice index`)**: cada clip se normaliza
  respecto al silencio principal (`video/normalize.py`), se decodifica a NV12
  (`video/cache.py`) y se indexa (`video/index.py`): descriptores de luma,
  vecinos, `d_ok` y apertura de boca por fotograma.
- **En tiempo real** (cuatro partes desacopladas):
  1. *Detección de voz* (hilo de audio): estado SILENT/SPEAKING con
     histéresis y `micro_pause` (`vad.micropause_release_ms`, 150-250 ms).
  2. *Selección* (director): decide el siguiente fotograma con datos ya
     calculados (anclas y descriptores por clip); sin búsquedas globales.
  3. *Gestos* (UI/atajos): solo dejan una petición bajo cerrojo; el director
     la atiende en su siguiente paso.
  4. *Composición* (hilo de render): `Director.step()` + efectos y envío. Sin
     lecturas de disco bloqueantes: los clips están en caché NV12 en memoria.
     El hilo de la interfaz solo lee métricas.

## 2. Componentes

| Módulo | Qué hace |
|---|---|
| `audio/features.py` | `MicroPauseDetector` (silencio ≥ `release_ms` dentro de una frase) y `MouthEnvelope` (apertura 0..1 por energía; se publica como métrica, el director ya no la usa para saltar). |
| `audio/pipeline.py` | Publica `micro_pause` y `mouth` junto al estado de voz. |
| `video/library.py` | Manifiesto `library/manifest.json`: grupos de silencio/habla (hasta 8 clips) y gestos con clip y atajo. |
| `video/normalize.py` | ECC euclídea (≤ 8 % desplazamiento, ≤ 4°) + transferencia de color Lab con ganancia limitada; entra en la clave de caché. |
| `video/index.py` | Descriptor 32×24, vecinos k fuera de una banda temporal, `d_ok` (p90 entre consecutivos), boca con Haar + píxeles oscuros. |
| `video/director.py` | Máquina de estados determinista (semilla y reloj inyectables), reproducción consecutiva, anclas, fundido corto de reserva, gestos y variantes `_hablando`, pausa manual, degradación y métricas de continuidad. |
| `gestures.py` | Catálogo de 8 gestos, variantes `<gesto>_hablando`, acciones reservadas (cancelar, pausa), atajos por defecto y validación de combinaciones. |
| `ui/hotkeys.py` | `RegisterHotKey` + filtro nativo de Qt (`WM_HOTKEY`); suspensión, reintento si otra aplicación libera la combinación. |
| `ui/gestures_tab.py`, `ui/tray.py` | Botones, atajos editables, menú «Gestos» en la bandeja, cancelar, pausar/reanudar video e indicador de pausa. |
| `record.py`, `ui/recorder_tab.py` | Planes guiados para clips principales, silencios/hablas largas y cada gesto; silueta de referencia en la vista previa. |
| `soak.py` | Prueba de resistencia simulada (reloj inyectado) o en tiempo real. |

## 3. Estados del director

```
                 ┌──────── pausa (atajo/UI) desde cualquier estado ────────┐
                 ▼                                                         │
          VIDEO_PAUSED ──reanudar──> TRANSITION (fundido desde la imagen congelada)
SILENT ──voz (en ancla)──> TRANSITION ──> SPEAKING
   ^  \                                     │  micro-pausa (en ancla) ─> SILENT
   │   └─gesto─> TRANSITION ─> GESTURE ──fin/cancelar──> TRANSITION ─> (según la voz)
   └──────────── silencio (en ancla) ───────┘
cualquier estado ──fallo de lectura──> DEGRADED ──> SILENT
```

- **SILENT / SPEAKING**: reproducen un clip del grupo correspondiente
  **fotograma a fotograma** (índice +1). Nunca se salta dentro de un clip.
  Al acabar un segmento de duración aleatoria se renueva el clip/segmento,
  también por un ancla.
- **TRANSITION**: fundido entre la última imagen mostrada y la entrada
  elegida (mínimo 2 fotogramas mezclados; más si las imágenes difieren). El
  flujo óptico DIS solo se usa si `video.transition_morph = "auto"` (por
  defecto `"off"`).
- **GESTURE**: reproduce el clip del gesto entero y vuelve al grupo que pida
  la voz. Cola de 1 (la petición nueva sustituye a la pendiente) y
  `cooldown_ms` para repeticiones.
- **VIDEO_PAUSED**: repite la última imagen válida (nunca un fotograma negro
  ni simulación de cortes). Cancela el gesto en curso y el pendiente; se
  rechazan gestos mientras dura. Al reanudar se entra por un ancla con
  fundido.
- **DEGRADED**: si un clip falla al leerse se retira, se mantiene la última
  imagen buena y se sigue con otro clip; la emisión no se corta.

### 3.1 Prioridades (de mayor a menor, en cada fotograma)

1. Pausa manual (pausar/reanudar).
2. Cancelar gesto.
3. Gesto en curso (no lo interrumpe la voz).
4. Gesto pendiente.
5. Voz (una micro-pausa cuenta como silencio).
6. Renovación de segmento.

### 3.2 Anclas, espera y cancelación

- **Anclas**: al crear el director se precalculan, por clip del grupo,
  hasta 48 fotogramas con boca cerrada y pose cercana a la neutra. Elegir la
  entrada solo compara descriptores de esas anclas.
- **Cambio pendiente**: un cambio de voz o de segmento se aplica en el
  siguiente fotograma ancla del clip actual; si no llega en
  `video.anchor_wait_ms` (180 ms, rango 150-200) se hace un fundido corto de
  `video.fallback_fade_ms` (200 ms, acotado a 150-200). Toda transición sin
  ancla dura exactamente ese fundido de reserva; con pocas anclas se entra
  por el principio o el final del clip con ese mismo fundido.
- **Gesto pedido hablando**: si existe `<gesto>_hablando` se usa en el
  siguiente paso; si no, se espera a una micro-pausa o a un ancla como mucho
  `SPEAKING_GESTURE_WAIT_MS` (170 ms) y luego se inicia el gesto base con
  fundido corto. Nunca se rechaza ni se aplaza; al terminar, si sigues
  hablando, se vuelve al grupo de habla.
- **Frases breves automáticas** (escenas `SPEAKING_ONE_SHOT` 6, 7 y 8): al
  pasar de silencio a habla, con probabilidad 0,35 y como mucho una cada
  20 s, se entra por «Respuesta corta» o «Frase corta» en vez del bucle de
  habla; en un cambio de segmento de una intervención de más de 8 s, con la
  misma probabilidad, «Terminar una explicación». Al acabar la frase, si
  sigues hablando, se entra en el grupo de habla por un ancla; si callas y a
  la frase le quedan más de 0,25 s, se sale al silencio con el fundido de
  reserva (si le queda menos, se deja terminar: su final ya cierra la boca).
  Un gesto pedido la sustituye en el siguiente fotograma. No cuentan como
  gestos pedidos. `gestures.auto_speaking_shots` las desactiva.
- **Rotación**: al renovar segmento se cambia de clip con probabilidad 0,7
  en silencio (alterna 1 y 3) y 0,6 en habla (2 y variaciones). La entrada
  es el ancla de menor coste respecto a la imagen actual, con penalización
  por repetir el mismo tramo de 2 s, así que no se vuelve siempre al
  fotograma 0.
- **Transición interrumpida**: si la voz cambia o se cancela el gesto en
  mitad de un fundido, el nuevo fundido parte de la imagen que se estaba
  mostrando (sin corte).
- **Métricas** (`DirectorStats`): transiciones en ancla, fundidos cortos,
  interrumpidas, cambios de segmento, discontinuidades (fotogramas no
  consecutivos fuera de una transición; debe ser 0), pausas, variantes
  usadas, espera y latencia de gestos.

## 4. Decisiones

- **Sin sincronía labial.** No hay reconocimiento de fonemas. La versión
  anterior saltaba entre fotogramas para acercar la apertura de boca a la
  energía de la voz; se retiró porque rompía la continuidad. Ahora la boca
  solo se cierra en micro-pausas, pasando por un ancla. No se afirma que la
  boca coincida con lo que se dice.
- **Selección, no síntesis.** Todo fotograma emitido es un fotograma real
  grabado por el usuario o una mezcla breve entre dos de ellos.
- **Reproducibilidad.** El director acepta semilla y reloj inyectados; las
  pruebas simulan 2 horas en segundos.
- **Compatibilidad.** `CONFIG_VERSION = 2`. Una configuración v1 se migra al
  cargar: `transition_morph = "auto"` pasa a `"off"`, las opciones
  `prerecorded_label` e `identify_prerecorded` se descartan, y los valores
  nuevos fuera de rango (`anchor_wait_ms`, `fallback_fade_ms`) se acotan. Sin manifiesto se usan los dos clips principales;
  `playback = "classic"` conserva el compositor anterior.
- **Salida limpia e indicadores locales.** La imagen emitida es siempre
  limpia: ningún módulo de `camvoice/video/` dibuja texto, figuras, bordes ni
  marcas de agua (lo comprueba una prueba que recorre su código), y no existe
  opción para rotularla (`--label` se eliminó). Los indicadores son locales:
  la ventana muestra
  siempre «● VIDEO PREGRABADO — la cámara «CamVoice» emite clips grabados, no
  la imagen en directo» mientras la cámara está activa, el estado de la
  cámara distingue «EMITIENDO VÍDEO PREGRABADO en la cámara virtual» de
  «Vista previa (VÍDEO PREGRABADO) — la cámara virtual no emite», y «⏸ VÍDEO
  EN PAUSA» durante la pausa; la bandeja muestra el estado y la consola las
  métricas. La pausa congela la última imagen emitida; no imita fallos de
  conexión.
- **Biblioteca de 62 escenas** (`scenes.py`, `video/importer.py`,
  `ui/library_tab.py`). La tabla de escenas es fija (número → acción,
  categoría, descripción). Solo se aceptan archivos `N.mp4` en la carpeta
  (sin recursión); `v_aud_*`, nombres con doble extensión, números omitidos
  (50, 52, 59, 63) y lo que no es video se ignoran y se cuentan. Un número
  repetido marca solo esa escena como «Error». Todo archivo que se
  decodifica queda «Listo» y activo; duración corta, cadencia irregular o
  resolución distinta se informan como avisos sin bloquear
  (`PROBE_VERSION = 3`). La validación guarda
  `probe_cache.json` (clave: ruta, tamaño, fecha y `PROBE_VERSION`), así que
  cancelar y volver a empezar no repite lo hecho. Importar es idempotente,
  conserva atajos y ajustes del usuario y deja una copia del manifiesto para
  «deshacer». Por defecto los videos se referencian, no se copian.
- **Cadencia variable.** La app Cámara graba a 28-30 FPS variables. La
  validación mide los intervalos de los primeros fotogramas: si la cadencia
  medida difiere más de un 5 % de la declarada se anota (se convierte a la
  cadencia de salida); se avisa (sin bloquear) si hay una congelación real
  (hueco > 250 ms) o más de un 30 % de huecos largos (> 1,4× la mediana).
  Limitación conocida: la caché remuestrea por índice de fotograma con la
  cadencia *declarada*; si la real difiere, el clip se reproduce un poco más
  rápido o lento (no hay audio, así que no hay desincronía, pero la deriva
  se acumula en clips largos). Remuestrear por marca de tiempo queda
  pendiente.
- **Ensayos de transición** (`video/trials.py`). Ejecutan el director con
  reloj simulado sobre los clips preparados y registran el par
  origen→destino, la espera hasta el fundido, su duración, si fue por ancla o
  de reserva y el coste de discontinuidad (distancia entre descriptores
  normalizada por `d_ok`; por encima de 4 se marca como alto). Los usa la
  pestaña Biblioteca y `camvoice transitions`.
- **Atajos seguros.** Por defecto `Ctrl+Alt+F1…F8`, pausar `Ctrl+Alt+F11` y
  cancelar `Ctrl+Alt+F12`. Se rechazan combinaciones con Win,
  `Ctrl+Alt+tecla del bloque principal` (equivale a AltGr y rompería la
  escritura), `Ctrl+letra` (atajos de Teams), teclas imprimibles sueltas y
  `Alt+F4`.

## 5. Limitaciones

- La boca no sigue las palabras (ver §4). Las micro-pausas solo cierran la
  boca si el clip tiene anclas cerca; si no, se usa el fundido corto.
- La apertura de boca del índice, que decide las anclas, es una heurística
  (Haar + píxeles oscuros): con barba, poca luz o la cara girada es menos
  fiable y puede haber menos anclas.
- El eco de los altavoces o la voz de otros asistentes puede activar la
  detección: se recomiendan auriculares (aviso en la pestaña Detector).
- La normalización corrige desplazamientos y diferencias de luz pequeños; no
  arregla clips grabados con otro encuadre o fondo.
- Duración del fundido: el fundido de reserva (sin ancla) dura siempre
  150-200 ms (3 fotogramas a 15 FPS). Una entrada en ancla entre dos
  fotogramas muy distintos (sobre todo gesto → silencio con gestos que no
  terminan en la pose neutra) se alarga hasta 5 fotogramas para no mostrar un
  corte: con los clips reales el p95 de todos los fundidos sigue en 333 ms
  (ver VALIDACION §8).
- Con las 62 grabaciones reales, hablar→callar suele acabar en fundido de
  reserva (18 de 20 ensayos): el clip de habla tiene pocos fotogramas con la
  boca cerrada y la pose neutra. Mejora grabando pausas con la boca cerrada
  dentro del clip de habla.
