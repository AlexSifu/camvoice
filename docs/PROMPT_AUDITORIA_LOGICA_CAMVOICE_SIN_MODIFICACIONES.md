# PROMPT MAESTRO PARA CLAUDE CODE — AUDITORÍA DE LA LÓGICA ACTUAL DE CAMVOICE

> **Modo obligatorio: DIAGNÓSTICO Y DOCUMENTACIÓN, SIN IMPLEMENTACIÓN.**
>
> Repositorio local: `D:\Git-Hub\camvoice`  
> Biblioteca de grabaciones: `C:\Users\USUARIO\Pictures\Camera Roll`  
> **Único archivo que se autoriza crear o modificar:** `docs/ANALISIS_FORENSE_LOGICA_CAMVOICE.md`

## 0. OBJETIVO Y CONTEXTO REAL

Quiero **comprender exactamente cómo funciona el CamVoice que está hoy implementado**, antes de autorizar cualquier cambio. NO quiero otro rediseño, refactorización, optimización, corrección ni implementación. Quiero un **diagnóstico técnico demostrable, basado exclusivamente en el código, configuraciones, manifest, pruebas y grabaciones que existen en mi equipo**.

El problema observado durante una prueba real es:

1. Al comenzar a hablar, el vídeo inicialmente reproduce una escena razonable.
2. Después de continuar hablando, la imagen empieza a mostrar **movimientos no solicitados**: levantar una mano, gesticular, ponerse una mano en la cara, etc.
3. Cuando cambia entre hablar y callar, **las transiciones siguen siendo abruptas y muy visibles**, incluso con la biblioteca de vídeos cargada.
4. Necesito identificar con precisión quién toma cada decisión, qué umbrales utiliza, de dónde obtiene el clip siguiente y por qué lo reproduce.

La biblioteca originalmente se numeró del 1 al 66; se grabaron **62 archivos numerados**. Faltan intencionalmente los números **50, 52, 59 y 63**. El archivo 58 sí existe. Pueden existir otros archivos ajenos a esta biblioteca: no los incorpores a conclusiones ni los modifiques sin identificar primero qué son.

En análisis anteriores se reportó, **aún no verificado en esta auditoría**: 601 tests aprobados y 5 omitidos; p95 de espera al fundido de 133 ms; p95 de fundido de 333 ms; 55 % de transiciones con reserva; 13 de 20 cambios hablar→callar con reserva; y simulación de dos horas sin negros. **No los presentes como resultados nuevos ni hechos confirmados:** encuentra las fuentes y los procedimientos reales, determina si corresponden a la versión actual y explica cómo reproducirlos.

## 1. REGLA INNEGOCIABLE: NO CAMBIAR EL PROYECTO

- Antes de iniciar, inspecciona `git status --short`, rama y commit actual. **No hagas commit, checkout, reset, stash, pull, push ni switch**.
- **No edites, formatees, refactorices, renombres, borres o regeneres** archivos de código, tests, manifest, configuraciones, índices, cachés, recursos, grabaciones ni ejecutables.
- **No modifiques la biblioteca real**, no muevas ni renombres los vídeos, no cambies sus tiempos, etiquetas ni metadatos. No importes ni prepares clips; únicamente inspecciónalos si es posible de manera no destructiva.
- No instales dependencias, no compiles, no inicies `install-camera`, no registres drivers, no reconfigures la cámara ni conectes Teams u otra plataforma.
- No ejecutes scripts que escriban archivos o cambien el estado operativo. Prefiere **inspección estática** del código y de las pruebas. Si un comando aparentemente diagnóstico puede regenerar cachés, modificar estado o escribir fuera del único informe, **no lo ejecutes**; explica qué faltaría comprobar.
- Puedes usar comandos de lectura (`Get-Content`, `Select-String`, `rg`, `git status`, `git log`, consultas de metadatos que no escriban) y razonar sobre las pruebas existentes, sin modificarlas.
- El **único archivo de salida permitido** es `docs/ANALISIS_FORENSE_LOGICA_CAMVOICE.md`. Si ya existe, actualízalo conservando cualquier información útil y sin sobrescribir cambios ajenos sin justificación.
- Si alguna funcionalidad mencionada aquí **no existe realmente en el código**, dilo explícitamente. No inventes clases, métodos, estados, opciones o eventos.

## 2. METODOLOGÍA OBLIGATORIA

### 2.1 Inventario del estado actual

Inspecciona la estructura del repositorio, punto de entrada, dependencias, versión de Python, `README.md`, `plan.md`, `docs/ARQUITECTURA.md`, `docs/DISENO_CAMVOICE_TIEMPO_REAL.md`, `docs/GUIA_GRABACION_Y_GESTOS.md`, configuración, manifest de escenas, scripts, pruebas y módulos realmente relevantes. **Los nombres son pistas, no prueba de que existan.**

Identifica explícitamente:

- Cómo se arranca la aplicación con `\.venv\Scripts\python.exe -m camvoice`.
- Qué ejecutan `dist\camvoice\camvoice.exe` y `camvoice-cli.exe`, si puede determinarse, y por qué pueden no coincidir con el código fuente vigente.
- Qué hace la interfaz y qué hace el motor de composición.
- Qué ocurre desde que se pulsa **Iniciar**, hasta que se emite el primer fotograma; y cómo se detiene.
- Qué estados operativos existen realmente y dónde se almacenan (cámara iniciada/detenida, detección de voz, compositor, gesto, pausa, transición, etc.).
- Qué hilos, tareas, callbacks y colas intervienen y qué frecuencia de actualización tienen.

### 2.2 Recorrido COMPLETO del micrófono a la imagen

Reconstruye con código verificable la ruta:

`micrófono → captura de audio → procesamiento / RMS o energía → VAD → histéresis / SpeechGate → estado solicitado → director / selector de clips → índice / fotograma ancla → transición / compositor → efecto visual → cámara virtual / vista previa`

Para **cada etapa**, informa:

- Archivo y función/método exacto, con **ruta relativa y números de línea**.
- Entradas, salidas y unidades (Hz, muestras, ms, FPS, dB, RMS, booleanos, etc.).
- Configuración que modifica el comportamiento, valor predeterminado, prioridad de overrides y si la UI cambia la configuración en tiempo real.
- Si depende del reloj real, del timestamp de audio, del número de fotograma, de un contador, de una semilla pseudoaleatoria o del estado anterior.
- Qué errores o inconsistencias pueden propagarse a la selección visual.

**Preguntas concretas que debes responder sin omitir ninguna:**

1. ¿Cómo detecta que **he empezado a hablar**? ¿Qué umbral o condición abre el estado de voz? ¿Hay agresividad VAD, RMS, volumen mínimo, energía, doble confirmación o ventanas deslizantes?
2. ¿Cómo detecta que **he dejado de hablar**? ¿Cómo mide la disminución del audio? ¿Qué representan exactamente `release_ms`, `attack_ms`, `min_speaking_ms` u otros parámetros existentes? ¿Cuánto tiempo persiste `SPEAKING` durante pausas?
3. ¿Qué ocurre si susurro, alzo la voz, río, respiro, toso, hablo muy seguido o digo solo «sí»? ¿Qué ocurre si se escucha a otra persona por los altavoces? ¿El detector diferencia quién habla o solamente reconoce actividad vocal?
4. ¿Cuándo y dónde se decide cambiar de `SILENT` a `SPEAKING`, y viceversa? Explica **cada transición intermedia**, su temporización y sus excepciones.
5. ¿Cuál es la **regla exacta de elección del siguiente vídeo**? ¿Orden secuencial, rotación, afinidad, pesos, aleatoriedad, duración, energía de voz, nivel de gesto o manifest?
6. ¿En qué momento decide **levantar una mano**, **tocarse la cara**, **asentir**, **sonreír** o **mirar a otro lado**? Determina si proviene de selección automática de un clip clasificado como habla/silencio, de una reacción automática, de un gesto solicitado con hotkey/UI, o de un defecto de asignación.
7. ¿Cómo se elige qué clip viene después cuando ya terminó el actual? ¿Existen tiempos mínimos/máximos, puntuaciones, enfriamientos o límites a repetir clips?
8. ¿El estado de voz controla la **boca real del clip** o solamente selecciona vídeos pregrabados? ¿Qué nivel de sincronización labial existe **en el código actual**?
9. ¿Qué ocurre si pido un gesto mientras hablo o si cambia el audio durante un gesto? ¿Qué prioridad prevalece? ¿Puede cancelarse o encolarse?
10. ¿Qué ocurre al poner vídeo en pausa y reanudar? ¿La imagen es un fotograma fijo o una nueva emisión? ¿Cómo se reincorpora al flujo?

### 2.3 Auditoría de las 62 grabaciones y su clasificación

Lee el manifest / biblioteca efectiva utilizada **en tiempo de ejecución**, no solamente la lista descrita en la documentación.

- Ubica los 62 archivos numerados reales, respetando sus extensiones y nombres exactos. No supongas que todos son `.mp4`.
- Registra los faltantes **50, 52, 59 y 63** como exclusiones intencionales. Verifica que el `58` exista.
- Identifica si los clips se referencian por número, nombre, UUID, rutas relativas, rutas absolutas, manifest, caché o índices.
- Para **cada uno de los 62**, incluye una fila en la tabla de mapeo: número, archivo, acción esperada, grupo/estado efectivo (`SILENT`, `SPEAKING`, `GESTURE`, otro), categoría/capacidad de reproducción automática, origen de la clasificación (archivo y línea), duración configurada/real si está disponible, y observación sobre incoherencias.
- Identifica específicamente qué IDs podrían reproducir **manos, cara u otros gestos durante habla normal**. Determina **cómo** entraron en la selección automática. Si no se puede verificar visualmente cada clip sin reproducirlo, separa esa limitación de la clasificación de código y del título asignado.
- Comprueba si los clips `hablando con una mano`, `hablando con ambas manos`, `hablando consultando notas`, `pensar con mano en mentón`, `tocarse la cara`, etc., pueden entrar en un pool de habla normal **sin acción explícita del usuario**.
- Comprueba si la biblioteca real cargada en la UI y la biblioteca cargada por el compositor son la misma; describe posibles rutas de configuración obsoleta, caché antigua, claves duplicadas, índices regenerados y discrepancias entre `dist` y ejecución desde Python.

### 2.4 Auditoría forense de transiciones abruptas

Determina la lógica **actual** de:

- Identificación de fotogramas ancla: criterios de boca cerrada, pose neutra, encuadre, exposición, tolerancias y precálculo.
- Búsqueda del ancla de salida y entrada: límites de tiempo, candidatos, restricciones, orden de prioridad y tratamiento cuando no encuentra ninguna.
- Fundido: duración fija o adaptativa, métrica de diferencia, fórmula de mezcla, límites, si trabaja por píxel/región/imagen completa, y por qué puede aparecer **doble exposición o cara fantasma**.
- Fundido de reserva (*fallback*): condiciones exactas de activación, coste de discontinuidad, tiempo máximo de espera, efecto de diferencias de postura o fondo.
- Selección de segmentos: reproducción lineal frente a saltos, comportamiento al terminar, bucle/pingpong, cambios durante habla continua, reserva de segmentos y repetición.
- Estabilización de encuadre, corrección de exposición/color, FPS, remuestreo y cadencias variables; ¿se ejecutan realmente o solo están anunciados?
- Diferencia entre el momento en que el detector pide cambiar, momento de ancla, momento de iniciar mezcla y momento de terminar la transición.

**Reconstruye dos cronologías concretas** usando el algoritmo y la configuración efectiva:

A. Hablo durante 20–30 segundos, me quedo callado: qué clips se reproducen, cuáles pueden entrar y qué ocurre al finalizar cada segmento.  
B. Estoy en silencio, digo «sí», hago una pausa de ~300 ms y vuelvo a hablar: qué eventos y estados se suceden.

No inventes tiempos medidos: separa **tiempos configurados/teóricos** de mediciones obtenidas de trazas o informes. Si los FPS impiden cumplir una espera en milisegundos de manera exacta, explica la discretización por fotogramas.

### 2.5 Interfaz de usuario y controles

Reconstruye el comportamiento real de cada sección existente en la interfaz (por ejemplo: Inicio, Cámara, Grabar, Vídeos/Biblioteca, Gestos, Salida, Efectos, Configuración, bandeja), **sin asumir que todas las pestañas existen**.

Para cada control que afecte al flujo, documenta: nombre visible, señal/callback, cambio de estado/configuración, persistencia, efecto inmediato o diferido, posible falta de retroalimentación al usuario y ruta de código.

Incluye un mapa de **atajos de teclado y botones**, indicando qué acción disparan y si pueden introducir un gesto inesperado, reproducir un clip diferente o interrumpir una transición.

### 2.6 Pruebas existentes y límites de evidencia

Inspecciona las pruebas y documentación de validación **sin modificarlas**. Registra:

- Qué pruebas prueban la **máquina de estados**, el audio, la biblioteca, gestos, transiciones, pausas y cámara virtual.
- Cuáles son **simuladas** y cuáles utilizan grabaciones reales o dispositivos reales.
- Qué se aserta realmente (por ejemplo, ausencia de negros, estado final, duración) y qué **NO** se comprueba (ghosting, discontinuidad visual, sentido del gesto, sincronía labial, identidad del clip reproducido).
- Si hay trazas o métricas previas confiables, localízalas, indica fecha, entorno, versión de código y límites.
- Explica qué datos adicionales harían falta para atribuir con certeza cada transición abrupta o gesto espontáneo a una rama concreta de código. **No añadas instrumentación en esta fase; solo especifica dónde convendría colocarla más adelante.**

## 3. CÓMO DOCUMENTAR LA EVIDENCIA

Todo hallazgo importante debe citar, en el propio Markdown final, su evidencia:

- `ruta/relativa/archivo.py:L120-L145` (o el rango real de líneas), símbolo o clave y valor leído.
- En manifest/configuración, identifica el archivo y el campo exacto.
- En tests, identifica función de prueba y qué verifica.
- En documentación, indica si coincide o **contradice** el código.

Clasifica cada conclusión como:

- **CONFIRMADO POR CÓDIGO**
- **CONFIRMADO POR CONFIGURACIÓN / MANIFEST**
- **OBSERVADO EN PRUEBA / REGISTRO EXISTENTE**
- **HIPÓTESIS CON EVIDENCIA PARCIAL**
- **NO VERIFICABLE SIN EJECUCIÓN / DISPOSITIVO**

No deduzcas que algo sucede solo porque hay un nombre de clase, una opción en README o un test simulado. Si una ruta o elemento citado no existe, registra esa ausencia.

## 4. ESTRUCTURA OBLIGATORIA DEL DOCUMENTO FINAL

Crea **únicamente** `docs/ANALISIS_FORENSE_LOGICA_CAMVOICE.md`, con contenido técnico en español, comprensible para el propietario del proyecto y suficientemente detallado para tomar decisiones después.

Debe contener, en este orden:

1. **Portada técnica:** fecha, repo, rama, commit, alcance, restricciones, metodología de solo lectura.
2. **Resumen ejecutivo:** comportamiento actual, hallazgos prioritarios, y qué se conoce / qué falta comprobar.
3. **Inventario verificable de módulos:** archivo, responsabilidad, función principal, relación con otras piezas.
4. **Arquitectura real:** diagrama `mermaid flowchart` desde micrófono hasta cámara virtual, con los módulos y decisiones que existan.
5. **Inicio, ciclo de vida y apagado:** qué activa la aplicación y la cámara, y cómo se detienen.
6. **Detección de voz explicada paso a paso:** RMS/VAD/umbrales/ventanas/ataque/liberación/histéresis y ejemplo temporal.
7. **Máquina de estados:** diagrama `mermaid stateDiagram-v2`, estados existentes, transiciones, prioridades y cancelaciones.
8. **Algoritmo de elección de escena:** lógica exacta, filtros, azar, repetición, comportamiento en habla prolongada y escucha.
9. **Catálogo real de 62 clips:** tabla exhaustiva y análisis de las escenas que pueden generar manos/cara sin petición explícita; 50/52/59/63 ausentes.
10. **Gestos, hotkeys y acciones automáticas:** cuándo se activa cada uno y desde dónde.
11. **Compositor y transiciones:** anclas, selección de fotogramas, fundidos, reserva, límites, diferencias entre código y documentación.
12. **Dos cronologías de ejecución:** habla prolongada → silencio; silencio → respuesta breve → pausa → habla.
13. **Interfaz y configuración efectiva:** qué hace cada control y dónde persiste.
14. **Matriz de hipótesis de los fallos observados**, con columnas: síntoma, causa candidata, evidencia, nivel de certeza, archivo relevante y prueba que permitiría verificarla.
15. **Cobertura y límites de pruebas actuales:** verificaciones reales vs simuladas; resultados históricos sin confundirlos con nuevos.
16. **Puntos de observabilidad recomendados** (solo propuesta): eventos/logs mínimos para saber `timestamp`, volumen/VAD, estado anterior y nuevo, ID clip seleccionado, motivo, gesto/tecla, ancla, fallback y tiempo de transición.
17. **Preguntas respondidas en lenguaje sencillo**: «¿cuándo sabe que hablo?», «¿cuándo sabe que callé?», «¿por qué levanta la mano?», «¿por qué se toca la cara?», «¿por qué salta la imagen?», «¿los 62 vídeos se usan automáticamente?», «¿qué pasa si hablo mucho?».
18. **Diagnóstico final, sin modificaciones:** causas confirmadas, causas probables, incertidumbres y **opciones de solución priorizadas SOLO COMO PROPUESTA**, cada una con archivos que habría que tocar y riesgos de regresión. No implementar ninguna.
19. **Anexo de trazabilidad:** índice de referencias `archivo:líneas`, tabla de configuraciones exactas y comandos de lectura utilizados.

Usa cuadros y tablas cuando hagan más clara la lectura. **Evita conclusiones vagas** como «el motor decide inteligentemente» o «se selecciona el mejor fotograma»; explica la comparación, ordenamiento, umbral o condición exacta, o reconoce si no se pudo determinar.

## 5. CRITERIOS DE ACEPTACIÓN

El análisis **NO está terminado** hasta que:

- Quede explicado de principio a fin el recorrido micrófono → voz → estado → escena → fotograma → transición → salida.
- Sepamos **qué mecanismo exacto** permite o impide que aparezca una mano durante habla continua, y los IDs/condiciones involucrados si el código lo permite.
- Sepamos cuándo se baja/cierra el estado de voz, con los parámetros y lógica reales.
- Sepamos por qué las transiciones pueden ser visualmente bruscas **pese a contar con 62 grabaciones**, distinguiendo fallos de clasificación, falta de anclas, fundidos y cambio de postura.
- Se hayan revisado los **62 mapeos** efectivamente usados, sin dar por supuesto que la importación corresponde a la numeración.
- El informe diferencie **hechos comprobados**, **hipótesis** y **limitaciones**.
- El único cambio registrado en Git producido por esta tarea sea el documento solicitado, si la carpeta estaba limpia. **No modifiques ni reviertas cambios que ya existían antes de la auditoría.**

## 6. ENTREGA Y MENSAJE FINAL

1. Guarda el documento terminado en `D:\Git-Hub\camvoice\docs\ANALISIS_FORENSE_LOGICA_CAMVOICE.md`.
2. Comprueba al finalizar con `git status --short` que no modificaste ningún otro archivo; compara con el estado inicial para no atribuirte cambios preexistentes.
3. **No comiences correcciones de código después de documentar**, aunque encuentres un defecto evidente.
4. Responde en el chat solo con:
   - Ruta del documento generado.
   - Confirmación de que el código, configuración, tests y vídeos no fueron modificados.
   - Tres a cinco hallazgos principales **con grado de certeza**.
   - Qué dudas no pudieron resolverse sin una prueba controlada posterior.

**Instrucción final:** investiga primero, explica después y no cambies el funcionamiento del proyecto. Este informe se utilizará para decidir las futuras correcciones, no para justificarlas retroactivamente.
