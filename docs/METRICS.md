# Métricas de rendimiento

Soporte de **CA-09**. Las cifras varían según el procesador y el equipo, así que
cada medición se registra junto a la identificación de la máquina donde se tomó.
Sin esa identificación un porcentaje de CPU no significa nada.

## Cómo medir

```
camvoice-cli bench --duration 300 --metrics                   # con cámara virtual
camvoice-cli bench --duration 300 --metrics --no-virtual-cam  # solo el motor
camvoice-cli bench --duration 1800 --metrics                  # CA-08: 30 minutos
```

El CSV queda en `%APPDATA%\camvoice\metrics\metrics-<fecha>.csv`, con una fila
por muestra.

### Las dos escalas de CPU

Confundirlas hace que la cifra no se pueda comparar:

- **% de un núcleo** — lo que devuelve `psutil`. Un proceso de un solo hilo
  saturando un núcleo da 100 %. Es la lectura **más exigente**, y es contra la
  que se evalúa el objetivo del 25 %.
- **% de la máquina** — dividido entre el número de núcleos lógicos. Es lo que
  muestra el Administrador de tareas de Windows y lo **comparable entre equipos**
  con distinto número de núcleos.

En un equipo de 8 núcleos lógicos, 10,6 % de un núcleo son 1,3 % de la máquina.
Las dos cifras describen lo mismo.

---

## Medición 1 — 2026-10-17, portátil de desarrollo

> **Este equipo SÍ cumple el perfil de referencia del plan**: Core i5 con
> gráficos integrados Intel. Se identificó al ejecutar el script de
> diagnóstico, que lee el modelo exacto del procesador.

### Equipo

| | |
|---|---|
| Procesador | **Intel Core i5-10300H @ 2,50 GHz** (Comet Lake-H) |
| Núcleos | 4 físicos / 8 lógicos |
| RAM | 23,8 GB |
| Gráficos | **Intel UHD Graphics** (integrada). Hay además una NVIDIA RTX 3050, **que la aplicación no usa**: todo el procesado es CPU (OpenCV sin CUDA, NumPy, copias de memoria) |
| Sistema | Windows 11 Pro de 64 bits (build 26300) |
| Python | 3.11.3 |

### Configuración

| | |
|---|---|
| Salida | 640 × 480 a 15 FPS, NV12 |
| Clips | 2 × 8 s sintéticos, 120 fotogramas cada uno tras preparar |
| Caché | 111 MB en total (55 MB por clip) |
| Detector | WebRTC VAD, exigencia 2, ventana 20 ms |
| Micrófono | USB (fifine) a 48 kHz, remuestreado a 16 kHz |
| Salida | Cámara virtual de OBS activa (sin OBS Studio abierto) |
| Duración | 90 s |

### Resultados

| Métrica | Valor | Objetivo | |
|---|---|---|---|
| FPS efectivos (media) | **15,02** | 15 | ✅ |
| FPS efectivos (mínimo) | 14,93 | — | ✅ |
| CPU, % de un núcleo (media) | **10,6 %** | < 25 % | ✅ |
| CPU, % de un núcleo (p95) | 21,9 % | — | ✅ |
| CPU, % de un núcleo (máximo) | 30,8 % | — | ⚠️ pico aislado |
| CPU, % de la máquina (media) | 1,3 % | — | — |
| Memoria RSS (inicio → fin) | 168 → 169 MB | — | ✅ |
| Deriva de memoria | **+0,36 MB/min** | < 1 MB/min | ✅ |
| Coste por fotograma (p95) | **2,05 ms** | < 66,7 ms | ✅ |
| Coste por fotograma (máximo) | 77,94 ms | — | ⚠️ ver abajo |
| Fotogramas emitidos | 1350 | — | — |
| Fotogramas tardíos | 2 de 1350 (0,15 %) | — | ✅ |
| Resincronizaciones del reloj | 0 | — | ✅ |
| Desbordes de audio | 0 | — | ✅ |

### Lectura

**Hay un margen enorme.** Con 2,05 ms de p95 por fotograma frente a los 66,7 ms
disponibles a 15 FPS, el motor usa un **3 % del presupuesto de tiempo**. Es la
consecuencia directa de la caché de fotogramas: en régimen estacionario el
trabajo por fotograma es una copia de memoria de 460 KB más el envío a la cámara
virtual, sin decodificador y sin conversión de color.

El margen permite 1280 × 720 a 30 FPS en este equipo sin acercarse al límite,
aunque a cambio de cuadruplicar la caché en disco.

**Los dos valores marcados con ⚠️ merecen explicación:**

- *Máximo de 77,94 ms en un fotograma, con 2,05 ms de p95.* Es **un** fotograma
  de 1350. El reloj lo absorbió sin resincronizar y los FPS efectivos no se
  movieron. Causas plausibles: la primera lectura del `memmap` desde disco antes
  de que el calentamiento surtiera efecto, o una pausa de planificación de
  Windows. No se repitió.
- *Máximo de CPU del 30,8 % de un núcleo, con 10,6 % de media.* Coincide con el
  arranque, donde se calienta la caché y se abre la cámara virtual. En régimen
  estacionario se mantiene en torno al 10 %.

**Las mediciones se tomaron con la máquina haciendo otras cosas** (esta misma
sesión de trabajo), así que son conservadoras: en un equipo dedicado a la
videollamada serían algo mejores.

### Hallazgo del detector durante esta medición

La primera pasada registró **22 cambios de estado en 90 s sin que nadie
hablara**: unas 12 activaciones falsas por minuto. El diagnóstico con
`vad-test` mostró la causa:

```
   t (s)  estado    trama     nivel    piso    SNR
    8.02  SPEAKING  voz       -48.6   -93.2   44.6
   11.02  SILENT    silencio -120.0   -92.2  -27.8
```

El nivel alternaba entre unos −50 dBFS y **exactamente −120 dBFS** (silencio
digital), signo de que algo en la cadena de captura recorta a cero entre
sonidos. El seguidor de piso de ruido lo perseguía hasta −117 dBFS, y a partir
de ahí cualquier transitorio aparecía con 45-60 dB de relación señal/ruido y
abría la puerta.

Se corrigieron dos cosas:

1. **Piso de ruido acotado a −75 dBFS.** Ningún entorno acústico real con un
   micrófono funcionando está por debajo; un piso que lo pretenda está midiendo
   la ausencia de señal, no el ruido de la sala.
2. **Puerta absoluta por defecto de −60 a −50 dBFS.** La voz conversacional está
   entre −30 y −15 dBFS, así que −50 deja pasar incluso un susurro lejano y
   descarta chasquidos y teclado.

Resultado tras la corrección, en el mismo equipo y la misma sala:

| | Antes | Después |
|---|---|---|
| Cambios de estado en silencio | 12,0 / min | **0** |
| Piso de ruido observado | −117 dBFS | −75 dBFS (acotado) |

Ambas correcciones tienen prueba automatizada en `tests/test_vad.py`.

---

## Medición 2 — 2026-10-17, misma máquina, cámara propia

Repetición con el filtro propio («CamVoice Virtual Camera», UnityCapture) en
lugar del de OBS, 60 s, misma configuración.

| Métrica | Cámara propia | Filtro de OBS | Objetivo |
|---|---|---|---|
| FPS efectivos (media) | **15,03** | 15,02 | 15 |
| CPU, % de un núcleo | **9,4 %** (p95 24,8 %) | 10,6 % (p95 21,9 %) | < 25 % |
| CPU, % de la máquina | 1,2 % | 1,3 % | — |
| Deriva de memoria | +0,69 MB/min | +0,36 MB/min | < 1 MB/min |
| Coste por fotograma (p95) | 5,03 ms | 2,05 ms | < 66,7 ms |
| Fotogramas tardíos | 6 de 894 | 2 de 1350 | — |

**Lectura.** El formato nativo de UnityCapture es RGBA en vez de NV12, así que
hay una conversión por fotograma que con OBS no existía. Medida aisladamente son
0,003 ms, despreciable; bajo carga real el p95 sube de 2 a 5 ms. Sigue siendo el
**7,5 % del presupuesto** de 66,7 ms, así que no se cambia el formato interno:
NV12 ocupa la mitad en la caché y conserva la ventaja con el backend de OBS.

La deriva de memoria subió de 0,36 a 0,69 MB/min, las dos por debajo del umbral
de 1 MB/min. Con 60-90 s de muestra **ninguna de las dos cifras distingue una
fuga real del ruido de medición**: hace falta la prueba de 30 minutos.

---

## Medición 3 — 2026-10-18, coste de los efectos

Mismo equipo. 60 s con desenfoque 45 %, grano 20 %, viñeta 30 % y saturación
menos 25 %, alimentando la cámara virtual.

### El camino de 76 % a 13,8 % de CPU

| Versión | CPU (% de un núcleo) | Coste p95 | FPS |
|---|---|---|---|
| Primera implementación | **76,4 %** (p95 99,6 %) | 28,75 ms | 15,08 |
| + desenfoque por reducción | 34,0 % (p95 54,2 %) | 7,48 ms | 15,11 |
| + `setNumThreads(1)` | **13,8 %** (p95 18,8 %) | 8,02 ms | 15,10 |

Sin efectos, para comparar: 10,7 % de un núcleo y 2,20 ms de p95.

**El margen de FPS nunca se movió** (entre 15,04 y 15,11 en las tres versiones).
Eso es lo que hace engañoso optimizar mirando solo la cadencia: el reloj
absorbía los 28 ms y la salida se veía perfecta mientras el proceso consumía
tres cuartos de un núcleo. La cifra que lo delata es la CPU, no los FPS.

### Coste por efecto, aislado, 640x480

| Efecto | ms/fotograma |
|---|---|
| Desenfoque 45 % | 2,40 |
| Desenfoque 100 % | 1,22 |
| Pixelado 60 % | 1,54 |
| Saturación | 1,27 |
| Viñeta 30 % | 1,14 |
| Brillo + contraste | 0,65 |
| Zoom 1,5x | 0,44 |
| Grano 20 % | 0,42 |
| **Todos juntos** | **5,60** |

El desenfoque al 100 % cuesta **menos** que al 45 %: a mayor sigma, mayor es el
factor de reducción previo y menos píxeles quedan por filtrar.

### Sobre la deriva de memoria

Las mediciones de 45 a 90 s dieron +0,36, +0,69, +1,17, +1,90 y +6,42 MB/min
**con la misma configuración**. El método —ajuste por mínimos cuadrados
extrapolado a un minuto— no resuelve el umbral de 1 MB/min en ventanas tan
cortas.

Se corrigió el informe para que declare **«no concluyente»** por debajo de
5 minutos, en vez de dar un veredicto que cambia de signo entre repeticiones.
Lo que decide CA-08 sigue siendo la prueba de 30 minutos.

---

## Mediciones pendientes

| Medición | Por qué hace falta |
|---|---|
| **300 s en el mismo i5** | Las mediciones son de 60-90 s. El perfil de hardware ya es el correcto; falta duración |
| **30 minutos continuos** | CA-08. Los 90 s medidos no pueden descartar una fuga lenta: una deriva de 0,36 MB/min podría ser ruido de medición o 11 MB en media hora. La prueba larga lo distingue |
| **Equipo con 8 GB de RAM** | Compatibilidad deseada. Con 169 MB de RSS más 111 MB de caché en la caché de páginas del sistema no debería haber problema, pero hay que verificarlo |
| **Con la videollamada en marcha** | El consumo que importa es el del conjunto: Teams o Zoom codificando el video que esta aplicación produce |
| **Clips reales de 15-20 s** | La caché crecería a unos 280 MB y conviene confirmar que la caché de páginas del sistema lo gestiona sin leer de disco en el bucle |

### Plantilla para registrar una medición nueva

```
## Medición N — <fecha>, <equipo>

### Equipo
| Procesador | | Núcleos | | RAM | | Sistema | |

### Configuración
| Salida | | Clips | | Caché | | Detector | | Micrófono | | Duración | |

### Resultados
(pegar la salida de `camvoice-cli bench`, que ya incluye el bloque
"Equipo de referencia")

### Lectura
(qué sorprende, qué no cumple, qué hay que investigar)
```
