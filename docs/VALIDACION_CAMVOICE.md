# Validación de CamVoice (modo director, gestos, reuniones largas)

Fecha: 2026-10-31. Equipo: Intel Core Ultra 9 185H (22 hilos), Windows 11
Pro, Python 3.12.10. **Solo se recogen resultados medidos en esta sesión.**
Lo que no se ha medido se indica como pendiente.

## 1. Pruebas automáticas

```bash
.venv/Scripts/python.exe -m pytest -o addopts="" --durations=5
```

Resultado (tras las directivas de producción: salida sin rótulos, 62 clips
listos, frases breves automáticas, rotación, fundido de reserva 150-200 ms):
**619 pasadas, 5 omitidas, 0 fallos, 55,5 s**. Las 5 omitidas son
la integración con la cámara virtual real (`CAMVOICE_TEST_REAL_CAMERA=1`).
No necesitan micrófono ni cámara (Qt en modo `offscreen`, atajos globales
desactivados con `CAMVOICE_NO_GLOBAL_HOTKEYS=1`).

Cobertura nueva, por archivo:

| Archivo | Qué comprueba |
|---|---|
| `tests/test_features.py` | Apertura de boca: silencio → 0, voz fuerte abre más, ruido sin voz no abre, sube rápido y baja suave, sílabas modulan |
| `tests/test_library.py` | Manifiesto: lectura, errores legibles, grupos, gestos, atajos |
| `tests/test_normalize.py` | ECC recupera desplazamientos conocidos, rechaza los grandes, color Lab acotado |
| `tests/test_index.py` | Descriptores, vecinos, `d_ok`, boca, cancelación, caché del índice |
| `tests/test_director.py` | Estados, transiciones, reproducción consecutiva, gestos (cola, cancelar, cooldown), degradado, hilos |
| `tests/test_stability.py` | Intervenciones cortas, habla continua, micro-pausas, gestos hablando (con y sin variante, espera ≤ 180 ms), peticiones simultáneas desde 8 hilos, transiciones interrumpidas, pausa manual (congelada, nunca negra, reanudar con fundido), 30 min simulados sin discontinuidades, misma semilla → misma secuencia, `MicroPauseDetector`, migración v1 → v2, variantes en biblioteca y consola |
| `tests/test_director_integration.py` | Motor completo con director, salida limpia (lo emitido = lo compuesto, sin marcas), pausa en el motor, soak simulado con pausas y soak corto en tiempo real |
| `tests/test_gestures.py` | Catálogo y validación de atajos (AltGr, Teams, Win, Alt+F4) |
| `tests/test_hotkeys.py` | Registro, despacho, ocupado por otra app, suspender/reanudar, sin fugas |
| `tests/test_record.py` | Planes de gesto (neutra→gesto→neutra), planes largos (>60 s), silueta de referencia |
| `tests/test_production_directives.py` | Fundido de reserva acotado a 150-200 ms y usado en toda transición sin ancla; gesto pedido hablando entra en ≤ 180 ms y vuelve al habla; frases breves automáticas (#6, #7, #8): solo las previstas, enlazan con el habla, se cortan al callar, un gesto las sustituye, se pueden desactivar; silencio largo alterna 1 y 3; habla larga rota variaciones sin volver siempre al fotograma 0 |
| `tests/test_transitions_v2.py` | Salida limpia: el motor no rotula, `--label` rechazado, opciones antiguas de rótulo ignoradas, ninguna llamada `cv2` de dibujo (texto, rectángulos, bordes…) en `camvoice/video/`, lo emitido es lo compuesto |
| `tests/test_scenes_import.py` | Importación: todo archivo decodificable queda Listo y activo (avisos sin bloquear), solo vacío o ilegible es Error, importación con archivo corrupto |
| `tests/test_trials.py` | Ensayos de transición (`camvoice transitions`) |
| `tests/test_library_tab.py` | Pestaña Biblioteca e informe de importación |
| `tests/test_ui.py` | Pestaña Gestos (botón y atajo de pausa), bandeja (acción e indicador de pausa), indicador de video pregrabado, sin opción de rótulo, grupos, preajustes VAD, grabar gestos/largos |

Todas estas pruebas son **simuladas** (caras sintéticas, reloj falso, Qt
`offscreen`); los resultados con las grabaciones reales están en §8.

## 2. Biblioteca de prueba (clips sintéticos)

Clips generados con `camvoice make-clips` y gestos sintéticos, a 320×240 y
15 FPS, 8 s. `camvoice index` preparó el modo director en 1,9 s:

| Clip | `d_ok` |
|---|---|
| silencio | 0,015 |
| silencio-2 | 0,044 |
| habla | 0,173 (rango de boca 0,29) |

4 gestos de 24 fotogramas; caché total 53 MB. Avisos correctos: los gestos
sintéticos «Saludar» y «Mirar arriba» no empiezan/terminan en pose neutra, y
4 gestos no tienen clip.

## 3. Resistencia simulada: 2 horas

```bash
CAMVOICE_HOME=<dir> .venv/Scripts/python.exe -m camvoice soak --simulate --duration 7200
```

Reloj inyectado, voz sintética (hablar a ratos y seguido, con micro-pausas),
un gesto cada ~45 s y una pausa manual de 5 s cada 10 min. Última
ejecución (código de salida 0):

| Métrica | Valor |
|---|---|
| Reunión simulada | 2,00 h en 12 s reales, 108 000 fotogramas |
| Voz en el guion | 35 % |
| Tiempo por estado | SILENT 61,5 %, SPEAKING 31,2 %, TRANSITION 4,0 %, GESTURE 2,6 %, VIDEO_PAUSED 0,8 % |
| Transiciones | 3044 (en ancla 2593, fundido corto 451, interrumpidas 292) |
| Discontinuidades / saltos | 0 / 0 |
| Cambios de segmento | 1266 |
| Gestos pedidos / hechos | 159 / 156 (0 rechazados, 0 atascados; la diferencia son peticiones sustituidas por otra antes de empezar) |
| Espera de gesto pedido hablando | máx 133 ms |
| Pausas manuales | 11 (825 fotogramas; 0 cambios durante la pausa; 0 negros) |
| Cambio por fotograma | máx 0,336, p99 0,252 |
| Imagen congelada más larga (fuera de pausas) | 0,0 s |
| Memoria Python (tracemalloc) | +136 KB, pico 365 KB |

## 4. Resistencia en tiempo real: 180 s

```bash
CAMVOICE_HOME=<dir> .venv/Scripts/python.exe -m camvoice soak --duration 180 --gesture-every 20 --quiet
```

Render real a 15 FPS con el director (sin cámara virtual ni micrófono):

| Métrica | Valor |
|---|---|
| Fotogramas | 2708 |
| FPS medio / mínimo | 15,00 / 14,99 |
| Fotogramas tardíos / resincronizaciones (perdidos) | 0 / 0 |
| Coste por fotograma p95 | 0,45 ms |
| Transiciones | 55 (en ancla 53, fundido corto 2, interrumpidas 1) |
| Discontinuidades / saltos | 0 / 0 |
| Gestos pedidos / hechos | 8 / 8 |
| Latencia del gesto (petición → primer fotograma) | p50 49 ms, p95 60 ms, máx 60 ms |
| CPU | media 1,4 %, p95 3,1 % de un núcleo |
| Memoria RSS | 116 → 118 MB (+0,47 MB/min en 3 min, no concluyente) |
| Modo degradado | 0 eventos |

(§2-§4 son mediciones anteriores con clips sintéticos; las de las 62
grabaciones reales y el código final están en §8.)

## 5. Atajos globales de Windows (prueba en vivo)

Script con la API Win32 real y el filtro nativo de Qt, usando
`Ctrl+Alt+Shift+F24` para no interferir con el equipo:

- Registro correcto (id `0xB100`).
- Un `WM_HOTKEY` enviado al hilo se despachó por el filtro de Qt en 1,1 ms y
  lanzó el gesto «saludar».
- Un segundo registro de la misma combinación se notificó como «ocupado por
  otra aplicación».
- Al liberar, la combinación se pudo registrar de nuevo.

**No probado:** pulsación física de las teclas con Teams/Zoom/Meet en primer
plano.

## 6. Pendiente de medir

- Sesión real de 1-2 horas con cámara virtual, micrófono y una aplicación de
  videollamada (FPS, CPU y memoria sostenidos). La prueba de 1 h en tiempo
  real sin cámara virtual se detuvo a petición: no hay resultado de 1 h.
- Latencia voz → boca con micrófono real (la edad del dato de voz figura como
  «n/d» con voz sintética).
- Revisión visual por una persona de las transiciones con los clips reales
  (las cifras de §8 son automáticas).
- Pausa y variantes `_hablando` con clips reales y pulsando las teclas con
  la aplicación de videollamada en primer plano.
- Tendencia de memoria RSS en 30 min o más: 5 min no bastan para concluir
  (en §8 sube; la memoria Python de la simulación de 2 h es estable).

## 7. Cómo reproducir

1. `python -m venv .venv` y `.venv\Scripts\python.exe -m pip install -r requirements-lock.txt`.
2. `.venv/Scripts/python.exe -m pytest`.
3. Con un directorio aislado: `set CAMVOICE_HOME=...`, `camvoice make-clips`,
   asignar gestos con `camvoice gestures --set saludar clip.mp4`,
   `camvoice index`, y los dos `camvoice soak` anteriores.
4. Con las grabaciones reales: los comandos de §8.

## 8. Grabaciones reales (62 clips) y código final

Equipo: Intel Core Ultra 9 185H (16 núcleos / 22 hilos), 31,7 GB RAM, Intel
Arc, Windows 11 Pro, Python 3.12.10. Biblioteca importada desde
`C:\Users\USUARIO\Pictures\Camera Roll` (62 videos; los números 50, 52, 59 y
63 no existen). Manifiesto: `%APPDATA%\camvoice\library\manifest.json`. Sin
cámara virtual ni micrófono (voz sintética). Todo es **medido**; lo marcado
«simulado» usa reloj falso con los clips reales.

### 8.1 Importación e índice (real)

| Paso | Resultado |
|---|---|
| Importar los 62 videos | 62/62 en 23,2 s. Tras las directivas, el manifiesto tiene 62 «Listo» y 62 activos |
| `camvoice index` | 209,3 s; caché 3608 MB |

### 8.2 Ensayos de transición (simulado)

`camvoice transitions --repeats 20`: 80 ensayos (hablar→callar, callar→hablar,
gesto→silencio, gesto→habla), 15 FPS.

| Métrica | Antes de las directivas | Código final |
|---|---|---|
| Entradas por ancla / fundidos de reserva | 67 / 13 | 61 / 19 |
| Espera hasta el fundido p95 / máx | 133 / 133 ms | 133 / 133 ms |
| Duración del fundido p50 / p95 | 67 / 333 ms | 133 / 333 ms |
| Hablar→callar: coste p50 | 7,56 | 2,58 |

Con `AUTO_SHOT_FINISH_S = 0,8` una ejecución intermedia dio una espera máxima
de 533 ms en hablar→callar (callar cerca del final de una frase breve
automática, que se dejaba terminar). Con 0,25 s el máximo medido vuelve a
133 ms. El fundido de reserva dura siempre 200 ms (3 fotogramas). El p95 de
333 ms corresponde a entradas en ancla entre poses muy distintas (gestos que
no terminan en la pose neutra), que se alargan hasta 5 fotogramas para no
mostrar un corte. El p50 sube de 67 a 133 ms porque ningún fundido sin ancla
baja ya de 150 ms.

### 8.3 Reunión de 2 h (simulado)

`camvoice soak --simulate --duration 7200 --gesture-every 45 --pause-every 600`

| Métrica | Antes de las directivas | Código final |
|---|---|---|
| Tiempo real empleado | 78 s | 25 s (108 000 fotogramas) |
| Transiciones (ancla / fundido corto / interrumpidas) | 2188 (995 / 1193 / 467) | 2238 (956 / 1282 / 486) |
| Discontinuidades / saltos | 0 / 0 | 0 / 0 |
| Gestos pedidos / hechos / rechazados | 159 / 156 / 0 | 159 / 156 / 0 |
| Espera de gesto pedido hablando (máx) | 133 ms | 133 ms |
| Frases breves automáticas (cortadas al callar) | — | 55 (34) |
| Pausas manuales (fotogramas; negros) | 11 (825; 0) | 11 (825; 0) |
| Imagen congelada máx fuera de pausa | 0,0 s | 0,0 s |
| Cambio por fotograma máx / p99 | 0,597 / 0,304 | 0,583 / 0,309 |
| Memoria Python | 0 → 547 KB (pico 1444 KB) | 0 → 546 KB (pico 1444 KB) |

La diferencia entre pedidos y hechos son peticiones sustituidas por otra más
reciente antes de empezar (cola de longitud 1), no rechazos. El tiempo real
empleado depende de la carga del equipo en ese momento.

### 8.4 Tiempo real, 300 s (real, sin cámara virtual)

`camvoice soak --playback {classic|director} --duration 300 --gesture-every 30 --quiet`

| Métrica | Clásico (antes) | Director (antes) | Director (directivas*) |
|---|---|---|---|
| Fotogramas | 4506 | 4507 | 4503 |
| FPS medio / mín | 15,00 / 14,90 | 15,00 / 14,97 | 15,00 / 14,97 |
| Tardíos / resincronizaciones | 0 / 0 | 0 / 0 | 0 / 0 |
| Coste por fotograma p95 | 3,74 ms | 2,65 ms | 2,72 ms |
| Transiciones (ancla / corto / interrumpidas) | — | 76 (39 / 37 / 10) | 78 (45 / 33 / 10) |
| Discontinuidades | — | 0 | 0 |
| Gestos pedidos / hechos | — | 9 / 9 | 9 / 9 |
| Latencia de gesto p50 / p95 / máx | — | 60 / 183 / 183 ms | 49 / 195 / 195 ms |
| CPU media / p95 (% de un núcleo) | 6,0 / 9,2 | 5,9 / 9,4 | 5,2 / 7,8 |
| Memoria RSS | 128 → 275 MB | 3321 → 3518 MB | 2339 → 2667 MB |

\* Con todas las directivas y `AUTO_SHOT_FINISH_S = 0,8`. El cambio posterior
a 0,25 s solo afecta a cuándo se corta una frase breve al callar; se midió en
8.2 y 8.3, no en tiempo real. La subida de RSS en 5 min (+24 a +51 MB/min) no
es concluyente: la caché de clips se mapea en memoria al reproducirse.
