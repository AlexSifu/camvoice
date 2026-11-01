# CAMVOICE — CORRECCIÓN EXCLUSIVA Y ROBUSTA DE TRANSICIONES VISUALES

> **Rol:** ingeniero sénior de vídeo en tiempo real, visión por computador y sistemas deterministas en Python/OpenCV.
>
> **Repositorio local:** `D:\Git-Hub\camvoice`
>
> **Objetivo único:** eliminar, hasta donde lo permite el material pregrabado, las **dobles exposiciones de rostro, saltos de cabeza/torso/encuadre y cambios de clip abruptos**, manteniendo estable el render en tiempo real.
>
> **No es un rediseño de CamVoice.** Es una corrección acotada del **selector de fotogramas, la planificación del cambio y la composición visual**. El usuario realizará las pruebas funcionales manualmente.

## 0. REGLA INNEGOCIABLE: NO TOCAR NADA MÁS

Antes de escribir, revisa `git status --short` y los diffs actuales. **Hay archivos nuevos no seguidos por Git**; en particular, `camvoice/video/director.py` podría figurar como `??`. Preserva todos los cambios existentes. No hagas `git reset`, `checkout`, `clean`, commits ni reescrituras masivas.

**Ámbito permitido, únicamente si es indispensable:**
- `camvoice/video/director.py` — planificación, compatibilidad, selección y render de transiciones.
- `camvoice/video/index.py` — descriptores geométricos, fiabilidad y anclas precalculadas, solo si hacen falta.
- `camvoice/video/normalize.py` y el mecanismo de **caché derivada** — solo si una alineación espacial/fotométrica de los clips lo exige y queda limitada al preprocesamiento.

No cambies ni reinterpretes:
- `camvoice/audio/**`, micrófono, VAD, WebRTC, `SpeechGate`, SNR, micro-pausas ni tiempos de detección.
- `camvoice/video/library.py`, `camvoice/scenes.py`, mapeo/categorías, escenas permitidas o excluidas, orden de los 62 vídeos y sus nombres.
- `camvoice/gestures.py`, control de gestos, atajos, sus prioridades, modo pausa o estado degradado.
- Interfaz PySide6, textos, configuración del usuario, manifest, instaladores, `dist/`, documentación o cualquier otra característica.
- Vídeos originales de `C:\Users\USUARIO\Pictures\Camera Roll`: **no modificar, convertir, cortar, renombrar ni sobrescribir**. Se permite leerlos y, si la arquitectura lo requiere, generar índices/cachés nuevas fuera de la carpeta original.
- Identificación existente de vídeo pregrabado, rótulos e indicadores de la aplicación.

**Prohibido** ejecutar `pytest`, `soak`, `bench`, ensayos de cámara, simulaciones largas o abrir CamVoice: **yo realizaré las pruebas funcionales**. Puedes usar lectura estática, análisis de pocos fotogramas reales como insumo de diseño, comprobación de sintaxis *sin generar archivos*, y `git diff --check` (cuando corresponda). No afirmes que una transición ya es imperceptible sin prueba visual.

## 1. EVIDENCIA QUE DEBES TOMAR COMO PUNTO DE PARTIDA

Lee íntegramente `docs/ANALISIS_FORENSE_LOGICA_CAMVOICE.md`, especialmente §§ 6–8, 11–12, 14 y 18. Comprueba la implementación **actual**: tras la auditoría hubo correcciones adicionales en `director.py` y `vad.py`; no presupongas que coinciden todas las líneas ni que el documento refleja la última versión.

**Síntomas vistos en la grabación de pantalla de aproximadamente 60 s:**

| Instante aproximado | Transición observada | Defecto visible |
|---|---|---|
| 4,55–4,90 s | habla, clip #5 → silencio, clip #1 | cabeza desplazada/escalada; doble imagen en el fundido |
| 27,1 s | cambio de postura/expresión | discontinuidad visible |
| 40,6–40,9 s | silencio, clip #3 → habla, clip #5 | salto de cabeza y torso |
| 56,1–56,3 s | gesto de saludo y retorno | discontinuidad en el encuadre corporal |

La grabación de pantalla puede no estar dentro del repositorio; **no inventes acceso** a ella. Si no está disponible, utiliza los vídeos reales del `Camera Roll` como material de lectura y el código/los registros para determinar las causas. No alteres clips para maquillar la validación.

**Hallazgos previos que requieren verificación con el código vigente:**
- El fundido de imagen completa con `cv2.addWeighted` superpone dos caras cuando la geometría no coincide.
- Los descriptores de miniatura **32×24** son insuficientes por sí solos para garantizar correspondencia de ojos, nariz, boca, cabeza y hombros.
- Los criterios de ancla se apoyan en cuantiles relativos por clip; una ancla del destino puede no ser realmente compatible con el origen.
- El cambio de habla a silencio recurre frecuentemente al fallback; una transición corta no elimina la doble cara si mezcla poses diferentes.
- Una transición anterior podía reiniciarse desde una imagen ya mezclada; **la corrección reciente de no refundir debe conservarse**.
- El contador de «0 discontinuidades» en las simulaciones **no demuestra** ausencia de doble exposición visual.

**No reactives** la selección automática de #38/#39/#40, las frases automáticas #6/#7/#8 ni el comportamiento anterior de micro-pausas. No reviertas la solución del `KeyError: 'gesture'` ni la recuperación de errores de render.

## 2. PRIMERO DIAGNOSTICA EL FALLO EN CADA RUTA VISUAL

Reconstruye con la implementación actual, antes de modificar, las rutas:

1. `SILENT (#1/#3) → SPEAKING`.
2. `SPEAKING (#2/#4/#5/#19/#20/#21/#22/#41) → SILENT`.
3. Rotación **dentro del mismo grupo**, habla→habla y silencio→silencio.
4. Entrada y salida de un `GESTURE` solicitado por el usuario.
5. Cambio de intención vocal mientras una transición ya está en curso.
6. Ausencia de candidata compatible dentro del plazo de espera.

Localiza las funciones existentes, o sus equivalentes reales: `Director.step`, `_advance`, `_build_anchors`, `_choose_entry`, `_entry_options`, `_pair_compatible`, `_execute_change`, `_step_transition`, mezclas, índices y normalización.

Para cada ruta determina: fotograma **real** origen, pose del fotograma destino, criterio de elección, disponibilidad de ancla, duración, política de fallback y qué fotogramas salen al sink. Distingue **corte geométrico**, **doble exposición**, **cambio de color/fondo** y **salto por rotación**. No confundas disminuir la frecuencia de transición con mejorar su calidad.

## 3. EVALÚA ESTAS ESTRATEGIAS COMO ALTERNATIVAS, NO COMO CAMBIOS ACUMULATIVOS A CIEGAS

Elige una política **determinista por calidad**, combinando únicamente lo que sea compatible con la arquitectura y el presupuesto del CPU. Debes razonar las opciones siguientes y **no** implementar todas indiscriminadamente:

| Estrategia | Cuándo aporta valor | Riesgo / condición |
|---|---|---|
| **A. Corte limpio entre poses realmente equivalentes** | Los rostros, ojos, escala, orientación y torso coinciden bien | En esos casos suele ser mejor que fundir dos rostros; requiere buen emparejamiento |
| **B. Buscar origen y destino en fotogramas ancla compatibles** | Hay varias anclas cercanas o puntos de entrada en clips destino | No basta un cuantil relativo; comparar el **par** origen-destino |
| **C. Seleccionar clips o subsegmentos según facilidad de conexión** | Hay varios vídeos autorizados para el mismo estado | No debe introducir saltos de fotogramas dentro de una escena ni mover manos espontáneamente |
| **D. Registro espacial suave en preprocesamiento** (traslación, escala y, si se justifica, rotación muy pequeña) | Encuadre ligeramente distinto pero pose próxima | Evitar deformar facciones; limitar transformaciones, documentar valores y usar caché derivada |
| **E. Normalización de luz y color** | Geometría ya compatible, pero cambia exposición/temperatura | No elimina salto geométrico; no usarla como sustituto del emparejamiento |
| **F. Fundido condicionado por similitud** | Pose **casi idéntica**; fondo o luz varían levemente | **Prohibir** el fundido cruzado global sobre caras incompatibles |
| **G. Composición selectiva por regiones/mascarilla** | Solo si existe una separación robusta y eficiente del sujeto/fondo | Bordes, cabello y manos pueden crear más artefactos; **no** imponerla sin evidencia |
| **H. Continuar clip actual hasta punto de salida favorable** | Hay ancla o pose compatible a poca distancia temporal | Espera corta y acotada; no congelar indefinidamente |
| **I. Fallback visual seguro y estable** | No existe pareja compatible dentro de la ventana permitida | Debe ser explícito, no generar doble rostro ni negro; puede quedar un corte visible |
| **J. Estabilización ligera de trayectoria entre tomas** | Pequeños desajustes translacionales de cabeza/torso | No inventar movimiento de labios ni usar deformación facial por flujo óptico |

**No habilites automáticamente Farneback, DIS, morphing facial, interpolación de ojos/boca, IA generativa ni fotogramas inventados.** Suelen desplazar o deformar ojos, dientes, boca y manos; no son una garantía de calidad.

## 4. CONTRATO TÉCNICO DE LA SOLUCIÓN

### 4.1 Compatibilidad de rostro y torso, no solo distancia global

Construye o mejora, de forma eficiente y preferentemente **offline** durante el indexado, una descripción por fotograma candidato que incluya, cuando exista evidencia fiable:

- Centro y tamaño de **cabeza/rostro**, distancia interocular o escala equivalente.
- Desplazamiento relativo de ojos, nariz y boca; orientación aproximada de cabeza, preferiblemente sin inferir expresiones inexistentes.
- Posición de hombros, parte visible del torso y manos **cuando sean detectables**; no equiparar una cabeza alineada con un cuerpo muy distinto.
- Apariencia del fondo separada del área del sujeto, exposición, color y nitidez.
- Boca razonablemente cerrada **cuando el cambio lo necesita**, pero no imponerla indiscriminadamente a los fotogramas que deben estar hablando.
- **Confianza de cada descriptor**: detecciones fallidas o ambiguas no deben aceptarse como coincidencia perfecta.

Si no hay un detector de landmarks disponible en dependencias ya instaladas, utiliza métodos clásicos de OpenCV y regiones de interés con sus límites de confianza. No descargues modelos, no añadas dependencias ni hagas rediseños fuera del alcance. Revisa el `cover` **16:9 → 4:3, 640×480**: compara la **geometría que realmente se emite**, no solo la fuente sin recorte.

Usa umbrales expresados en unidades interpretables y/o normalizadas por el tamaño de cara, calibrables desde los clips reales disponibles; no uses un `cost ≤ 4` global como único criterio. Evita aceptar anclas solo porque son «las menos malas» dentro de un vídeo malo. Si los descriptores no son fiables, **marca incompatibilidad**.

### 4.2 Índice de conexiones y selección reproducible

- Precalcula **candidatas de entrada y salida** de los clips habilitados para el grupo real de destino.
- Prioriza minimizar la diferencia **entre el último fotograma real de origen y el primero real del destino**, no solo la cercanía al fotograma 0 del clip #1.
- No elijas candidatos aleatoriamente cuando haya una diferencia de calidad relevante; usa ordenamiento estable y desempate determinista. Respeta la configuración de `seed` si la arquitectura la usa.
- Evita segmentos que empiecen o acaben con gestos, parpadeos a mitad, manos en una pose incompatible o cambios bruscos de escala.
- No cambies la categoría de clips ni reproduzcas un vídeo prohibido; el índice **solo** clasifica opciones permitidas.
- Evita un barrido completo de miles de fotogramas y el cálculo de visión compleja por cada tick de render: mantener como mucho un conjunto pequeño de opciones ya precalculadas.
- Si cambia la versión de los descriptores, **invalida de forma segura la caché derivada obsoleta** sin afectar los originales; la lectura de índices antiguos debe fallar de forma controlada y regenerarse en el flujo normal.

### 4.3 Planificador de transición

Debe decidir **cuándo** se cambia y **cómo**, de forma separada:

1. Mantener la reproducción lineal normal del clip actual.
2. Buscar un **punto de salida cercano y compatible** con un punto de entrada real del destino. Espera acotada por la configuración vigente (objetivo aproximadamente 150–200 ms, sin sobrepasarla arbitrariamente).
3. **Si ambas poses son muy compatibles:** permitir un **corte limpio** o una transición mínima de 0–1 fotogramas mezclados, según cuál preserve mejor la continuidad. No forzar un fundido por principio.
4. **Si solo hay pequeñas diferencias de encuadre/iluminación:** usar únicamente las correcciones espaciales o de color que sean seguras y que estén disponibles sin carga por tick.
5. **Si las poses son incompatibles:** **no** hacer `cv2.addWeighted` de las dos caras ni utilizar fundidos más largos para esconder la diferencia. Escoger el mejor fallback determinista, limitado en duración, que emita siempre fotogramas válidos, sin quedarse atascado y sin introducir un gesto o una tercera escena arbitraria. Puede ser un corte visible si el material no permite algo mejor; reconocerlo explícitamente.
6. Nunca iniciar una transición desde una **imagen ya compuesta/mezclada**. Mantener el arreglo anterior: resolver una transición en curso y tratar solicitudes nuevas de manera controlada, sin cascadas de fundidos ni alternancia frenética.
7. Al alternar clips del **mismo estado** no crear rotaciones que aporten poca variedad pero provoquen saltos grandes: postergar/omitir **esa rotación visual** si no existe candidato razonable, sin impedir un cambio de estado solicitado por la voz.
8. La entrada/salida de un gesto manual debe utilizar los mismos criterios de continuidad **sin retrasar o cancelar el gesto arbitrariamente**. No cambies su disparo, su contenido ni las prioridades existentes.

**Invariantes:** sin pantalla negra; sin repetición o pérdida incontrolada de frames; sin cambio de resolución; sin movimiento no solicitado; sin bucles infinitos; no congelar imagen indefinidamente; no degradar la estabilidad del hilo de render.

### 4.4 La mezcla debe ser una excepción, no la estrategia predeterminada

Si aún existe una rutina general con `cv2.addWeighted`, deja ese mecanismo **condicionado a compatibilidad geométrica alta**. Comprueba que la combinación de fotogramas no cree:

- Dos contornos faciales, dos posiciones de ojos o boca.
- Cabezas de diferente escala o posiciones discordantes.
- Hombros/manos duplicados o que aparezcan repentinamente.
- Cambios repentinos del fondo por desalineación.

Un umbral único de variación de luma del fotograma completo **no basta** para decidir. Una transición de baja calidad debe ser **rechazada antes de componerla**, no simplemente contabilizada después.

### 4.5 Calidad temporal y costo

- Mantén la cadencia configurada (actualmente 15 FPS) y compatibilidad con memoria de fotogramas `memmap`/NV12 donde aplique.
- En cada tick: selección acotada, operaciones predecibles y cero búsquedas exhaustivas o detección facial nueva.
- No introduzcas `sleep`, I/O sincrónico de disco, carga de modelos o transformaciones pesadas por fotograma en `director.step`.
- Asegura que cualquier fallo de detector/índice caiga en una estrategia simple que **mantenga la emisión**, conservando la recuperación de error actual.
- Evita normalización que aplique escala distinta entre imágenes consecutivas del mismo clip: las transformaciones por toma deben ser estables, no fluctuar por frame.

## 5. DECISIÓN SOBRE UNA COMBINACIÓN REALMENTE INCOMPATIBLE

Se necesita una política clara para el caso crítico **hablar → callar**:

- Si en el plazo permitido no hay una salida cercana y una entrada de silencio cuya cabeza/torso coincidan, **no existe una transición invisible garantizable** con las grabaciones disponibles.
- Nunca resolver esa limitación con caras transparentes superpuestas, deformaciones de boca, fotogramas sintéticos, congelaciones prolongadas o una secuencia de más gestos.
- Elige la opción **menos defectuosa** de manera determinista, priorizando ausencia de doble rostro, continuidad de estado y latencia acotada, y deja constancia de que esa toma necesita un punto de entrada/salida mejor si es imposible mejorarla por software.
- No penalices el habla real, el silencio real o los controles manuales con esperas indefinidas por buscar perfección visual.

## 6. CRITERIOS DE ACEPTACIÓN PARA MI PRUEBA MANUAL POSTERIOR

No ejecutes esas pruebas: **impleméntalo de forma que yo pueda verificarlas después**.

| Escenario que probaré | Lo que debe observarse |
|---|---|
| `#5 → #1`, al callar | Sin rostro duplicado durante el cambio; postura lo más consistente posible |
| `#3 → #5`, al hablar | Sin salto evidente de escala/posición causado por una selección deficiente |
| Hablar durante varios minutos | La rotación de clips autorizados no provoca gestos ni cambios corporales abruptos evitables |
| Hablar, pausa corta, volver a hablar | No hay refundidos en cascada y se conserva la lógica de micro-pausas ya corregida |
| Saludar manualmente (`#17`) y regresar | El compositor no duplica mano/cabeza; el gesto se ejecuta normalmente |
| Clips muy distintos entre sí | Estrategia de reserva segura: no dobles caras, no negro, no bloqueo y degradación reconocible si el material es incompatible |

Esos resultados **no** se deben declarar logrados por el simple hecho de compilar o de pasar verificaciones estáticas. No manipules contadores para mostrar «0 discontinuidades». Si el sistema ya ofrece una métrica, mantén su significado; puedes mejorar la exactitud **solo dentro de los módulos visuales autorizados**, sin alterar la interfaz ni fabricar resultados.

## 7. ORDEN DE EJECUCIÓN OBLIGATORIO

1. **Inspecciona** la rama, árbol de trabajo, funciones relevantes, manifiesto (solo lectura), índices actuales y el informe forense. Conserva todas las correcciones anteriores.
2. **Determina** qué parte del salto nace del recorte, del emparejamiento, de la geometría, del color, de la elección aleatoria y/o de la mezcla. Prioriza evidencia de los 62 clips reales. No inventes un diagnóstico si no puedes verificarlo.
3. **Selecciona y justifica internamente** la combinación mínima de estrategias de la tabla del §3 con mayor probabilidad de reducir los defectos sin modificar la arquitectura de audio ni la UI.
4. **Implementa los cambios** únicamente en la capa visual autorizada; utiliza las funciones existentes siempre que sea posible y conserva compatibilidad hacia atrás con el índice/caché.
5. **Haz solo revisión estática** de los archivos editados y `git diff --check` cuando sea pertinente. No generes cachés de `pytest`, no inicies el render, no ejecutes la cámara ni pruebas de larga duración.
6. **Finaliza el trabajo en esta ejecución**, no dejes un plan sin código ni prometas trabajo en segundo plano. Si una parte requiere hardware o mi prueba manual, déjala identificada como pendiente de validación, no como resuelta.

## 8. RESPUESTA FINAL DE CLAUDE — BREVE Y HONESTA

Muestra **solamente**:

1. **Archivos modificados** y función de cada cambio.
2. **Causa(s) exacta(s)** comprobadas, separando hechos de hipótesis.
3. **Qué estrategia visual** se usa ahora para: pose compatible / pose parcialmente compatible / pose incompatible; especifica si queda algún fundido global.
4. **Qué no tocaste:** VAD, UI, gestos, escenas, manifest, vídeos, ejecutables.
5. **Comprobaciones estáticas realizadas** y la frase: «No ejecuté pruebas funcionales; quedan para tu validación manual».
6. **Limitación restante concreta**, si el material no permite continuidad suficiente, indicando los clips implicados cuando se pueda fundamentar.
7. **Estado final:** `IMPLEMENTACIÓN TERMINADA — PENDIENTE DE PRUEBA MANUAL` (o explica exactamente qué impidió terminar).

**No agregues otras funciones ni vuelvas a abordar el micrófono. El único objetivo es corregir la calidad visual de la transición.**
