# PROMPT MAESTRO PARA CLAUDE CODE — CAMVOICE

## 0. Rol, objetivo y modo de trabajo

Actúa como **arquitecto de software senior, ingeniero de vídeo/audio en tiempo real, especialista en Python/OpenCV y responsable de calidad (QA/SRE)**. Tu tarea es **analizar, diseñar, implementar, integrar, probar y documentar** mejoras de producción **directamente sobre el repositorio existente CamVoice**, ubicado en:

`D:\Git-Hub\camvoice`

**No entregues únicamente un análisis, un documento de diseño o una lista de recomendaciones.** El entregable principal debe ser **código real y funcional integrado al proyecto**, con pruebas y documentación, sin romper las funciones que hoy funcionan.

Tu fuente de verdad es el código del repositorio. El análisis anterior que se resume a continuación describe posibles problemas y rutas de archivos, pero **verifica todo contra la versión actual** antes de cambiar nada. Si algo no existe, cambió de nombre o no se corresponde con el código, adapta el plan a la realidad y explica las diferencias.

### Restricciones obligatorias

- Mantén el funcionamiento **100 % local y sin conexión durante las reuniones**.
- **Sin IA generativa, sin servicios cloud, sin GPU obligatoria, sin APIs externas y sin transmisión de audio o vídeo a terceros**. Prioriza algoritmos clásicos, deterministas y eficientes en CPU; reutiliza las dependencias existentes.
- Conserva compatibilidad con Windows y con el método de cámara virtual, interfaz, configuración y empaquetado que realmente tenga el proyecto.
- No reescribas desde cero ni cambies de framework por iniciativa propia.
- Respeta la privacidad: evita almacenar micrófono o conversaciones salvo consentimiento y una opción explícita. Añade una indicación clara de cuándo se está usando vídeo pregrabado o una fuente virtual; no diseñes mecanismos para ocultar ese hecho ni para eludir verificaciones de presencia.
- **No inventes resultados de pruebas** ni afirmes latencias/FPS sin medición. Diferencia lo implementado, lo probado automáticamente y lo pendiente de probar en hardware real.
- Evita cambios destructivos. Antes de editar, revisa `git status`; no hagas `git reset --hard`, `git clean`, force-push ni borres archivos ajenos. No realices commits ni pushes salvo que se solicite expresamente.

## 1. Contexto funcional que debes respetar

CamVoice muestra vídeo local pregrabado en una cámara virtual. Su funcionamiento actual, según un análisis preliminar, alterna entre dos clips: **SILENT** (silencio) y **SPEAKING** (habla). La detección de voz se usa como un interruptor y no sincroniza palabras ni movimiento de labios.

Escenario real de uso:

- Reuniones de **3 participantes**, normalmente **1–2 horas**, con atención visual centrada en los mosaicos de vídeo y sin pantalla compartida.
- La persona alterna respuestas muy breves, pausas, intervenciones medianas y períodos largos de habla continua.
- Se necesitan acciones voluntarias activadas por **teclado o ratón**, como saludar, sonreír, asentir, negar, mirar hacia arriba o hacia un lado; no basta con dos clips automáticos.
- El paso de hablar a guardar silencio resulta brusco: saltan la postura, la boca, la luminosidad o el fondo, y algunas transiciones producen una doble imagen.
- El movimiento actual de la boca no acompaña las pausas ni la energía de la voz; a veces permanece en movimiento después de hablar o reacciona a voces ajenas recogidas por el micrófono.
- La repetición de secuencias cortas se hace evidente a lo largo de sesiones prolongadas.

**Importante:** No prometas sincronización fonética perfecta a partir de clips arbitrarios. Implementa primero una coordinación audiovisual técnicamente honesta y medible; si faltan materiales o hay límites fundamentales, incorpóralos claramente al producto y a la documentación.

## 2. Hallazgos previos que tienes que comprobar

El análisis anterior mencionó estos archivos y valores (son hipótesis hasta que los confirmes):

- `audio/gate.py`: detección/puerta de voz (VAD).
- `video/composer.py`: composición y máquina de estados.
- `video/clip.py`: gestión de clips, reproducción o bucles.
- `config.py`: umbrales y parámetros.
- `docs/GUION-GRABACION.md`: instrucciones de grabación.
- `resume_window_ms = 1500`: reinicio frecuente del clip de habla en el fotograma inicial.
- `release_ms = 450`, `min_speaking_ms = 400`, `transition_ms = 160`, con salida original alrededor de **15 FPS**.
- Posible modo de bucle `pingpong`, con inversión abrupta de dirección.
- Una transición mediante mezcla lineal entre cuadros que no comparten postura, fondo o exposición.

Comprueba también README, entrypoints/CLI, grabación, conversión, caché, salida de cámara virtual, interfaz de bandeja, hotkeys, pruebas, dependencias, packaging y cualquier otro componente impactado. Haz un mapa real de dependencias antes de editar.

## 3. Auditoría inicial obligatoria

Ejecuta e informa, de forma resumida:

1. `pwd`, `git status`, rama actual, estructura del proyecto, versión de Python, herramientas y dependencias disponibles.
2. Qué partes ya funcionan y qué pruebas automáticas existen; ejecuta una línea base **antes** de los cambios si el entorno lo permite.
3. Mapa de flujo de datos: micrófono → análisis/VAD → estado → selector de fotogramas → compositor → cámara virtual, y ruta de UI/hotkeys → gesto → compositor.
4. Riesgos de compatibilidad, bloqueos de audio, accesos concurrentes, búferes, reloj de fotogramas, carga CPU, fallos de clips y pérdida de frames.
5. Requisitos que pueden implementarse con los clips existentes y los que **necesitan nuevas grabaciones**.

Crea o actualiza un documento de diseño técnico versionado, por ejemplo `docs/DISENO_CAMVOICE_TIEMPO_REAL.md`, pero **continúa inmediatamente con la implementación**: el diseño no debe ser el resultado final.

## 4. Arquitectura objetivo

Propón y aplica una arquitectura modular, testeable y extensible que aproveche lo existente. Introduce módulos nuevos solo si están justificados. Separa responsabilidades:

- **AudioCapture / AudioFeatures:** adquisición robusta del micrófono, envolvente de energía/RMS, actividad vocal, suavizado, timestamps y métricas.
- **SpeechGate:** decisión con histéresis de inicio/fin, respuestas cortas, control de ruido, protección contra falsos positivos; configurable.
- **ClipLibrary / CacheIndexer:** clips de silencio, habla y gestos; manifest, validación de FPS/resolución, metadatos y preparación offline.
- **VisualFeatures:** características de pose, boca, rostro, fondo y exposición cuando se pueda calcular sin GPU ni IA generativa.
- **TransitionPlanner / MotionGraph:** selección temporalmente coherente de puntos de enlace y rutas de fotogramas, evitando saltos arbitrarios.
- **GestureController:** solicitudes manuales, colas/prioridades, interrupciones y cancelación.
- **Compositor / PlaybackEngine:** máquina de estados y render con tiempos correctos, sin I/O pesado en el hilo de entrega.
- **UI / Hotkeys / Config / Diagnostics:** controles visibles, ajuste, errores y telemetría local sin datos sensibles.

Si los nombres existentes son mejores, consérvalos; importa más la separación de responsabilidades y la compatibilidad que introducir clases con estos nombres exactos.

### Máquina de estados

Define explícitamente, con eventos y transiciones comprobables:

- `IDLE` o `SILENT`.
- `SPEAKING`.
- `TRANSITION` (o mecanismo equivalente sin estados inconsistentes).
- `GESTURE`, con retorno automático al contexto correcto.
- `DEGRADED` / fallback ante falta de medios, carga o error.

Reglas clave:

- Un gesto manual puede solicitarse durante habla o silencio; termina y **retorna según el estado de voz actualizado**, no según el que existía al iniciarlo.
- Los gestos no deben quedarse congelados ni repetirse accidentalmente.
- Cambios de habla durante una acción no deben romper el render ni reentrar de forma caótica.
- Define prioridad, cola limitada, cancelación y *cooldown* de gestos repetidos; documenta qué sucede si se solicita uno mientras otro está activo.
- Los cambios de estado nunca deben bloquear el hilo de frames ni generar fotogramas corruptos.

## 5. Implementación funcional requerida

### 5.1. Voz, pausas y coordinación visual de boca

- Ajusta VAD para **intervenciones muy breves y habla prolongada** con histéresis configurable; elimina retrasos innecesarios, pero sin introducir parpadeo de estados.
- Implementa extracción de intensidad/energía vocal en ventanas cortas, asociadas a timestamps **monotónicos**; suaviza ruido sin convertir sílabas en una señal plana.
- Usa esa señal como entrada para elegir **secuencias visuales compatibles** con niveles de apertura de boca, manteniendo continuidad espacial/temporal y límites máximos de salto.
- Precalcula fuera de la reproducción los índices necesarios: apertura de boca, similitud visual, candidatos de transición y referencias a clips; evita barridos completos por cuadro.
- **No confundas RMS con fonemas**: describe con claridad el nivel de sincronización alcanzable; si una solución de visemas requeriría nuevo material o modelos adicionales, déjala como fase opcional documentada y no finjas haberla implementado.
- Evalúa latencia desde el audio capturado hasta el fotograma producido y ajusta el búfer o compensación de retardos de forma medible.
- Impide que una actividad breve provoque un clip de habla de duración artificialmente larga. Cuando el habla se detiene, la boca debe tender a posición neutra con una transición visualmente estable.
- Maneja variaciones de ruido, micrófono desconectado, silencio extendido y dispositivos de captura distintos.
- Añade guía de uso de auriculares y calibración del micrófono. **No asumas que un VAD normal puede distinguir al dueño del micrófono de las voces reproducidas por altavoces**; si la separación no es fiable, comunica la limitación y ofrece configuración segura. La captura de audio de salida mediante WASAPI, si existe, debe ser opcional y con una justificación clara.

### 5.2. Transiciones entre clips sin saltos visibles

- Preprocesa alineación del encuadre/fondo y normalización de color/exposición con métodos clásicos de OpenCV cuando los materiales lo permitan.
- Valida límites de esta alineación: **no deformar la cara de forma inaceptable**, no forzar una transformación si la geometría o el fondo no coincide.
- Genera candidatos de transición a partir de similitud entre fotogramas, postura/rostro, boca, continuidad de movimiento y proximidad temporal.
- Prioriza entradas/salidas con boca neutra y pose compatible; evita entrar siempre por el fotograma 0.
- Usa mezclas adaptativas solo entre fotogramas suficientemente compatibles; una disolución larga entre poses distintas no se considera una solución válida.
- Si pruebas flujo óptico, compara coste CPU y artefactos frente a una transición clásica; deja **fallback** a la opción más robusta.
- Corrige o reemplaza el efecto de rebote brusco de `pingpong` por cierres de bucle compatibles o estrategias de transición elegidas por similitud.
- Mantén el fondo estable siempre que la grabación lo permita; no uses extracción de fondo si introduce halos o contornos extraños.
- Agrega métricas calculables: error de color entre planos, diferencia en región facial, discontinuidad geométrica, saltos de movimiento y duración de transición.

### 5.3. Gestos manuales naturales y completos

Implementa la biblioteca de gestos, **solo a partir de clips reales autorizados por la persona que aparece en ellos**, con estos nombres lógicos iniciales:

- `saludar`
- `sonreir`
- `asentir`
- `negar`
- `mirar_arriba`
- `mirar_izquierda` / `mirar_derecha`
- `reir` (opcional si hay material apropiado)
- `neutro` / `cancelar` / retorno

Requisitos:

1. Descubrimiento de clips mediante **manifest versionado**; no rutas dispersas ni nombres hardcodeados por toda la aplicación.
2. Validación de presencia, decodificación, dimensiones, duración, FPS y pose de entrada/salida; mensajes claros si un gesto todavía no está grabado.
3. Interfaz y/o menú de bandeja para ver y lanzar acciones; añade **atajos configurables**, evitando colisiones con atajos de Windows/Teams y manteniendo control por ratón.
4. Clip de gesto de reproducción **una vez**, con retorno contextual y transición visual; no *pingpong* para acciones puntuales.
5. Respuesta rápida sin bloquear audio ni vídeo; registra latencia solicitud→primer cuadro del gesto.
6. Si falta un gesto, informa que no está disponible; **no inventes ni simules una ejecución correcta**.
7. Incluye una forma visible de suspender/desactivar atajos y de cancelar una acción en curso.

### 5.4. Material para sesiones de 1–2 horas

- Permite varios clips para cada categoría (en particular, silencio y habla) e indexación de **múltiples puntos de entrada compatibles**.
- Construye recorridos temporales coherentes entre segmentos reales; evita tanto el reinicio repetido al primer cuadro como el salto aleatorio sin continuidad.
- Añade variación limitada y reproducible con semillas configurables para testing. Sin movimientos inverosímiles ni cambios bruscos de pose.
- Introduce política de selección que reduzca repeticiones próximas, sin usar historial ilimitado que aumente memoria durante dos horas.
- Define manejo de clips cortos y biblioteca insuficiente: fallback visual estable, diagnóstico y recomendaciones de nueva grabación.
- Si es necesario, actualiza `docs/GUION-GRABACION.md` con especificaciones de grabación **realistas** para neutro, habla, gestos, encuadre, foco, exposición, iluminación, postura, principio/fin de cada clip, duración y coherencia de FPS. Distingue mínimos obligatorios de material adicional recomendado.

### 5.5. Grabación, caché y compatibilidad

- Conserva lectura de activos existentes o añade migración automática/versionada del manifest y caché.
- Haz la generación de metadatos/indexación **offline y cancelable**, con barra de progreso/log y recuperación ante archivos corruptos.
- No guardes una matriz NxM de similitud entre todos los fotogramas si crece demasiado: usa representaciones reducidas, índices de candidatos, segmentación o cálculo incremental.
- Mantén un presupuesto de memoria definido; no cargues a RAM dos horas de vídeo descomprimido.
- Verifica que cualquier cambio de formato preserve los caminos de CLI, interfaz y cámara virtual anteriores; incluye pruebas de compatibilidad con configuración legacy.

### 5.6. Rendimiento y resiliencia

- **Evalúa** salida a 24 o 30 FPS si el equipo/stack lo permite; no lo impongas a costa de estabilidad. Implementa FPS configurable y preserva el modo anterior como fallback.
- Mantén composición consistente con reloj monotónico, timestamps, control de *frame pacing*, cola acotada y política explícita para frames tardíos.
- Minimiza asignaciones por fotograma y trabajo pesado dentro del bucle de envío.
- Evita hilos huérfanos, *deadlocks*, condiciones de carrera, referencias a frames liberados y pérdidas de dispositivos no controladas.
- Si falla un clip, el índice, el micrófono, los hotkeys o la cámara virtual, usa fallbacks definidos, informa en UI/log y evita un cierre inesperado.
- Implementa indicadores de rendimiento local: FPS real, frames perdidos, latencia estimada de voz, transiciones, cargas de CPU/memoria y errores sin capturar conversaciones.

## 6. Plan de ejecución: debes escribir el código

Trabaja por **fases incrementales completas**, revisando pruebas después de cada fase:

**Fase A — Baseline y compatibilidad.** Auditoría, reproducción de problemas, tests de regresión y configuración segura.

**Fase B — Audio.** Extracción de características, VAD ajustable, corrección de pausas y sincronización temporal básica.

**Fase C — Vídeo.** Preprocesado de clips, normalización, selección coherente de puntos de entrada y transiciones sin saltos.

**Fase D — Gestos.** Manifest, estado `GESTURE`, clips de reproducción única, hotkeys/UI, cancelación y fallback.

**Fase E — Sesiones extensas.** Multi-clip, continuidad y variación no repetitiva, optimización de memoria y CPU.

**Fase F — Integración final.** Empaquetado, test integral, soak test, documentación y reporte técnico.

No presentes cada fase como un simple roadmap. **Implementa cuanto sea posible en esta ejecución**, priorizando una solución funcional completa de extremo a extremo. Si hay una barrera genuina (biblioteca de vídeo no grabada, dispositivo de cámara virtual ausente o dependencia inaccesible), termina las partes implementables y automatizables, deja un fallback válido y señala el impedimento exacto. No te detengas únicamente a solicitar aprobación del diseño si hay información suficiente para proceder.

## 7. Pruebas mínimas obligatorias

Crea o amplía pruebas automatizadas que cubran, como mínimo:

| Área | Casos obligatorios |
|---|---|
| VAD/voz | silencio, ruido, «sí» breve, pausas internas, monólogo, cambios rápidos, desconexión |
| Sincronización temporal | timestamps, retrasos, audio irregular, caída/duplicación de frames |
| Selección visual | entradas compatibles, fallo del índice, ausencia de candidatos, continuidad, determinismo con semilla |
| Transición | poses iguales/distintas, diferencia de luz, sin cuadros vacíos/doble imagen por mezcla inapropiada |
| Gestos | lanzar en SILENT y SPEAKING, retorno correcto, cancelación, spam de atajos, faltantes |
| Biblioteca | manifest inválido, caché legacy, FPS distintos, códec fallido, clip corrupto |
| Robustez | excepción del decodificador, dispositivo ausente, reinicio de captura, cierre ordenado |
| Regressions | CLI, UI, parámetros existentes, cámara virtual y cualquier test previo |

Además:

1. Construye **fixtures sintéticos** de vídeo/audio para ejecutar pruebas sin webcam, Teams ni material facial real.
2. Diseña y, si el entorno permite ejecutarlo, corre una **prueba de estrés de 2 horas** con transiciones y gestos simulados; recopila memoria, FPS, frames perdidos y errores. Cuando no sea práctico correrla completa, deja el comando automatizado y ejecuta una prueba reducida de forma transparente.
3. Incluye tests de concurrencia para que las solicitudes de gesto y los eventos de voz no degraden el motor.
4. Establece umbrales de calidad de forma razonada con mediciones de baseline, no promesas arbitrarias: latencia de gesto, retardo de cierre, estabilidad de FPS, uso de RAM y porcentaje de frames perdidos.
5. Reporta comandos **reales** ejecutados, resultado y limitaciones del entorno. Nunca marques «aprobado» sin ejecución o evidencia verificable.

## 8. Calidad de ingeniería no negociable

- Código modular, tipado cuando aplique, responsabilidades bien definidas, docstrings útiles y nombres claros.
- Configuración centralizada y validada; valores por defecto seguros, sin números mágicos dispersos.
- Interfaces testeables, inyección de dependencias si aporta valor, pocos acoplamientos entre UI/audio/vídeo.
- Errores explícitos y logs estructurados; evitar `except: pass`, bucles ocupados, fugas y callbacks que bloqueen el render.
- No eliminar pruebas para conseguir que el conjunto pase; no desactivar controles existentes injustificadamente.
- No agregar dependencias grandes o que requieran descargas en runtime sin explicar y justificar el coste; preferir componentes ya instalados.
- Mantener las convenciones existentes y la compatibilidad del programa instalado.
- Cambios pequeños y verificables, con diff revisado al final; no incluir archivos temporales, binarios de prueba ni grabaciones personales en Git.

## 9. Entregables exactos dentro del repositorio

1. **Código implementado** (motor de voz, transiciones, clips/índices, gestos y controles) integrado al flujo existente.
2. **Configuración** actualizada con compatibilidad razonable y ejemplos.
3. **Pruebas** unitarias, de integración y prueba de resistencia automatizable.
4. `docs/DISENO_CAMVOICE_TIEMPO_REAL.md`: arquitectura real, componentes, estados, diagrama de flujo, limitaciones y decisiones.
5. `docs/GUIA_GRABACION_Y_GESTOS.md` (o ampliación de la guía existente): material necesario y procedimiento de preparación/validación.
6. `docs/VALIDACION_CAMVOICE.md`: pruebas ejecutadas, comandos, resultados, problemas conocidos, FPS/latencia medidos y pasos de reproducción.
7. README y ayuda CLI/UI actualizados con instrucciones de puesta en marcha y explicación de atajos.

No generes documentos ficticios sobre funciones sin implementar. El contenido debe corresponder con el código real.

## 10. Formato de tu respuesta final

Cuando acabes, responde con:

1. **Diagnóstico confirmado:** qué hallazgos eran reales y cuáles cambiaron tras leer el código.
2. **Implementación realizada:** rutas exactas de archivos editados/creados y resumen de la función de cada cambio.
3. **Estado funcional por requisito:** voz, pausas, transiciones, gestos, bucles largos, FPS, robustez — cada uno como implementado, parcial o pendiente con causa.
4. **Evidencia de calidad:** comandos realmente ejecutados, resultados de tests y métricas disponibles.
5. **Uso paso a paso:** cómo preparar clips, generar índices, configurar gestos, ejecutar CamVoice y comprobar la cámara virtual (según comandos reales del repositorio).
6. **Limitaciones honestas:** sincronización labial fonética no garantizada, nuevos clips requeridos y validaciones que exigen Windows/Teams o hardware que no esté presente.
7. **Resumen de `git diff` y `git status`** finales; no hacer commit/push de manera automática.

### Criterios de aceptación principales

Se considera útil y terminado solo cuando:

- La aplicación continúa arrancando y emitiendo vídeo bajo el flujo existente (o el bloqueador de hardware queda aislado y documentado).
- Las respuestas cortas, pausas y tramos largos tienen una reacción de vídeo coherente con el estado de voz y sin cambios temporales absurdos.
- Existen gestos manuales reales, lanzables con mouse/teclado, cuya ausencia de clips se maneja sin fallos.
- Se han eliminado o mitigado con validación las transiciones de pose/iluminación incompatibles y los reinicios sistemáticos al primer frame.
- Hay una estrategia ejecutable para 1–2 horas que no cargue todo el vídeo descomprimido en memoria.
- No se introducen nuevas regresiones conocidas y existe evidencia de las pruebas que sí se pudieron ejecutar.
- Está documentado qué depende todavía de nuevos vídeos de origen y qué no es técnicamente posible con clips pregrabados.

**Empieza ahora inspeccionando el repositorio y continúa hasta dejar implementada y validada la solución más completa posible. No cierres tu trabajo únicamente con una propuesta de arquitectura.**
