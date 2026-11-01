# CAMVOICE — CORRECCIÓN QUIRÚRGICA DE LA LÓGICA (SIN PRUEBAS NI CAMBIOS COLATERALES)

> **Instrucción para Claude Code.** Trabaja exclusivamente en el repositorio local `D:\Git-Hub\camvoice`. Este encargo **sí autoriza modificar código**, pero **solo** para corregir los comportamientos detallados aquí. **No ejecutes pruebas**: el usuario abrirá CamVoice y hará las pruebas manualmente.

## 0. Objetivo y restricción principal

Corregir **únicamente** la lógica responsable de:

1. Reproducir movimientos de manos o escenas inesperadas mientras la persona habla normalmente.
2. Activar automáticamente clips de frases cortas o cierre sin que se soliciten.
3. Saltar repetidamente de `SPEAKING` a `SILENT` y viceversa ante micro-pausas normales de la voz.
4. Reiniciar un fundido sobre un fotograma ya mezclado cuando cambia el estado de voz.
5. Seleccionar fotogramas «ancla» visualmente incompatibles, generando saltos o doble exposición.

**No añadas funcionalidades nuevas. No rediseñes la UI. No alteres las grabaciones ni la biblioteca del usuario. No cambies otros comportamientos.** Es una corrección puntual, no una refactorización general.

## 1. Fuente obligatoria: auditoría ya realizada

Antes de editar, lee entero:

`docs/ANALISIS_FORENSE_LOGICA_CAMVOICE.md`

El informe describe el **árbol de trabajo actual** (no solo el commit HEAD). Sus referencias son orientativas: **comprueba cada método y su implementación real antes de tocarlo**, porque el proyecto puede haber cambiado desde la auditoría.

Lugares relevantes reportados:

| Tema | Código señalado en la auditoría |
|---|---|
| Grupos de clips por categoría | `camvoice/video/library.py`, especialmente `pools()` |
| Catálogo y números de escenas | `camvoice/scenes.py` |
| Máquina de estados, acciones automáticas, micro-pausas, cambios de clip | `camvoice/video/director.py` |
| Anclas, elección de entrada y composición | `camvoice/video/director.py` (`_build_anchors`, `_choose_entry`, `_step_transition` y equivalentes actuales) |
| Valor por defecto de acciones automáticas | `camvoice/config.py` (`auto_speaking_shots`) |
| Fuente de voz (`SpeechGate`, micro-pausa) | `camvoice/audio/gate.py`, `features.py`, `pipeline.py` — **inspección para entender, no modificación salvo estricta necesidad justificada** |

El repositorio contiene **cambios locales sin commit y archivos nuevos**. Respétalos todos. No reviertas, limpies, sobrescribas ni formatees trabajo ajeno.

## 2. Cambios autorizados (alcance exacto)

### C1. Separar el habla normal de los movimientos de manos

Según la auditoría, el grupo `SPEAKING` incluye actualmente estas grabaciones:

`#2, #4, #5, #19, #20, #21, #22, #38, #39, #40, #41`.

Los clips **#38 («Hablar con una mano»), #39 («Hablar con ambas manos») y #40 («Hablar consultando notas»)** no deben participar **en ninguna rotación automática ni elección automática al reanudar el habla**.

El grupo de habla automático debe quedar restringido, con los datos disponibles, a:

`#2, #4, #5, #19, #20, #21, #22, #41`.

El grupo de silencio automático debe conservar `#1` y `#3`.

**Obligatorio:**

- Aplica la exclusión en el **punto efectivo de selección o construcción de pools**, no solo en una etiqueta de la interfaz.
- Asegúrate de que se respete en **todas** las rutas automáticas: inicio de habla, reanudación tras silencio, micro-pausas, rotación periódica y recuperación de estado.
- Los clips excluidos **siguen existiendo** en la biblioteca; no borres registros, rutas, archivos, categorías del manifiesto ni grabaciones. No inventes nuevos atajos.
- Los gestos explícitos a petición (saludar, sonreír, asentir, tocarse la cara, etc.) deben seguir siendo manuales y funcionar como hasta ahora.
- No habilites asentimientos u otros gestos aleatorios. Respeta que `auto_nod` está desactivado.
- Si la forma real de identificar escenas no usa números directamente, utiliza sus identificadores estables y verifica la correspondencia antes de aplicar el filtro. No dependas únicamente del nombre del archivo ni del título de pantalla.
- Si falta algún vídeo autorizado, preserva el fallback existente de forma segura y no permitas un pool vacío.

### C2. Desactivar las frases automáticas por defecto

La auditoría confirma que `auto_speaking_shots` se considera `True` cuando la clave no está en la configuración. Esto puede iniciar sin solicitud los clips **#7, #8** y el cierre **#6**.

**Cambio requerido:**

- Cambia el **valor por defecto en el código** a `False`, de manera que una configuración que no define la clave **no active** estas acciones.
- Si una configuración existente define de manera **explícita** `auto_speaking_shots: true`, respeta esa elección; no borres ni modifiques el archivo de configuración del usuario.
- Conserva el comportamiento **a petición** de esas escenas, si existe en la implementación actual.
- No cambies probabilidades, tiempos o controles visuales que ya no tengan efecto sobre la opción desactivada, salvo lo indispensable para evitar ejecuciones automáticas accidentales.

### C3. Micro-pausas: no cambiar de grupo mientras `SpeechGate` siga en `SPEAKING`

Se observó esta lógica: una micro-pausa de aproximadamente **200 ms** hace que el director desee `SILENT` aunque `SpeechGate` siga en `SPEAKING`. Eso genera viajes breves habla → silencio → habla y una nueva elección de clip.

**Cambio requerido:**

- La decisión de **grupo** (`SPEAKING` o `SILENT`) debe seguir el estado **estable** entregado por `SpeechGate`, no el indicador puntual de micro-pausa.
- Usa las micro-pausas, si resultan útiles, **solo como señal para preferir un punto seguro de transición ya solicitada**, sin originar por sí mismas un cambio de grupo.
- Conserva los parámetros efectivos del micrófono, VAD y puerta de voz (`attack_ms`, `release_ms`, `min_speaking_ms`, `gate_db`, etc.). **No recalibres ni cambies sus valores**; la corrección está en la interpretación del director.
- No reinicies el clip ni vuelvas a sortear otro al aparecer una micro-pausa si `SpeechGate` permanece en `SPEAKING`.
- Cuando la puerta declare de verdad `SILENT`, sigue siendo obligatorio pasar a silencio de forma acotada y segura, sin mantener indefinidamente un vídeo de habla.
- No afirmes que esto sincroniza los labios con las palabras: únicamente evita cambios de estado espurios.

### C4. No reiniciar transiciones desde una imagen ya mezclada

La auditoría identifica que, si cambia la voz durante `TRANSITION`, el compositor puede iniciar un **nuevo fundido desde el fotograma previamente mezclado** (`_enter_from_frozen(..., anchor=False)` o lógica equivalente), produciendo ghosting múltiple.

**Cambio requerido:**

- Evita **fundido sobre fundido**. Durante un fundido ya iniciado, registra el último estado deseado como una petición pendiente, pero **no utilices un fotograma sintético mezclado como origen de otra mezcla**.
- Completa la transición actual de forma determinista y, al finalizar, atiende el último estado solicitado **solo si continúa siendo necesario**.
- Evita cambios innecesarios dentro del mismo grupo y solicitudes pendientes obsoletas; no generes bucles o colas de transiciones.
- Respeta las prioridades y cancelaciones existentes para `GESTURE`, `VIDEO_PAUSED` y `DEGRADED`. No cambies el comportamiento manual de pausar/reanudar ni el de cancelar gestos.
- Si hay una ruta de emergencia por error de fotograma, conserva su recuperación segura; no emitas pantalla negra.
- Mantén la respuesta de voz dentro de los límites actuales en condiciones normales. No introduzcas una espera ilimitada para eliminar artefactos.

### C5. Exigir compatibilidad visual real en las anclas de cambios automáticos

Actualmente las «anclas» se definen mediante **cuantiles relativos a cada clip**. Un fotograma puede calificar como ancla aunque la cabeza o las manos sigan en una pose que no combina con el clip de destino.

**Cambio requerido, limitado al selector/compositor existente:**

- En transiciones automáticas entre habla y silencio, y en rotaciones automáticas, **prioriza pares de fotogramas con pose y encuadre compatibles con la referencia neutra** (clip #1, según la auditoría), además de boca razonablemente cerrada cuando corresponda.
- Evita considerar válido un par de anclas solo porque ambos sean relativamente «los mejores» dentro de sus clips. Verifica **compatibilidad entre el fotograma de origen y el de destino** antes de decidir el fundido.
- Aprovecha los descriptores o índices **ya precalculados**. **No añadas análisis global por fotograma dentro del hilo de render**.
- Si no existe una pareja suficientemente compatible dentro de la espera ya permitida, conserva un **fallback acotado, seguro y determinista**. No prolongues indefinidamente el estado anterior, no emitas negro y no garantices invisibilidad si las tomas no son compatibles.
- Evita introducir un fundido de imagen completa **más largo** como supuesta solución universal: puede aumentar la doble exposición.
- **No** incorpores morph, Farneback, DIS, modelos de IA, interpolación facial, nuevo tracking, cambios de resolución/FPS, regrabación, reajuste de luces ni cambios en el preprocesamiento general.
- Limita este ajuste a las **transiciones automáticas**. No alteres el recorrido ni los puntos inicial/final de gestos manuales que están fuera de esta corrección.

## 3. Archivos y datos protegidos

**Archivos de código que puedes editar, solo si realmente lo requiere la solución:**

- `camvoice/video/director.py`
- `camvoice/video/library.py`
- `camvoice/config.py`
- `camvoice/scenes.py` **solo si resulta imprescindible**; preferir no editarlo.

No edites otros módulos sin una dependencia técnica absolutamente inevitable; si surge una necesidad fuera de esta lista, **no la implementes de forma silenciosa**: consígnala como pendiente y detente en ese punto, sin ampliar el alcance.

**Prohibido modificar:**

- `C:\Users\USUARIO\Pictures\Camera Roll\` (los vídeos numerados; especialmente no recortar, mover, renombrar o convertir).
- `%APPDATA%\camvoice\config.json` y `%APPDATA%\camvoice\library\manifest.json` (configuración y biblioteca real del usuario).
- `camvoice/ui/**` (interfaz), `camvoice/audio/**` (captura, VAD, puerta y micrófono), `camvoice/video/normalize.py`, `index.py`, `cache.py`, scripts, instaladores, ejecutables, carpetas `dist/` y `build/`.
- `README.md`, `docs/**`, `plan.md`, `tests/**`, `pytest.ini` y dependencias.

No regeneres cachés ni índices; no importes de nuevo los 62 vídeos. El usuario ya tiene la biblioteca cargada. No hagas cambios cosméticos, renombrados extensivos, limpieza de código o reformateo global.

## 4. Forma de trabajar (obligatoria)

1. Inspecciona `git status --short`, el informe forense y **solo** las rutas pertinentes de código. Identifica los cambios locales preexistentes y presérvalos.
2. Ubica las condiciones exactas que producen C1–C5. Confirma si siguen presentes antes de editar; no implementes soluciones basadas en suposiciones.
3. Haz el **mínimo diff posible**, manteniendo APIs y flujo existentes. No reestructures el proyecto.
4. Revisa el diff final de los archivos que tú modificaste para verificar que no se hayan colado cambios adicionales. Puedes usar `git diff`, `git diff --check` y lectura estática; **estos no deben convertirse en ejecución de la aplicación**.
5. **NO EJECUTES** `pytest`, tests unitarios, pruebas simuladas, benchmarks, `soak`, ensayos de transiciones, build, instaladores, GUI, cámara virtual, scripts de indexación/importación ni tareas de 1–2 horas. Tampoco ejecutes `git add`, `git commit`, `git reset`, `git clean`, `git checkout` ni operaciones que descarten trabajo.
6. Al terminar, responde inmediatamente al usuario. **No digas que probaste lo que no probaste**, ni presentes métricas inventadas o anteriores como resultado de esta corrección.

## 5. Comportamiento esperado para la validación MANUAL del usuario

Sin realizar pruebas tú, deja la lógica preparada para estos escenarios:

| Escenario | Comportamiento que debe producir la lógica |
|---|---|
| Habla continua 30–60 s | Solo escenas automáticas de habla permitidas; nunca #38/#39/#40 ni #6/#7/#8 por defecto. |
| Pausa natural de 200–300 ms dentro de la frase | Se conserva el grupo de habla mientras `SpeechGate` siga en `SPEAKING`; no se barajan clips por esa pausa. |
| Deja de hablar de verdad | Cuando `SpeechGate` pasa a `SILENT`, cambio acotado a #1/#3 con selección de anclas compatible o fallback seguro. |
| Voz vuelve durante un fundido | No se inicia otro fundido desde un fotograma mezclado; se atiende al estado vigente al terminar. |
| Saludo/sonrisa/asentimiento solicitado manualmente | Sigue funcionando por la ruta de gestos existente, sin eliminar clips ni cambiar atajos. |
| Pausa manual de vídeo | Continúa congelando el último fotograma válido y reanudando como antes. |
| No hay un ancla buena | Fallback de duración acotada y sin imagen negra; ninguna promesa falsa de continuidad perfecta. |

## 6. Criterios de aceptación y límites honestos

- **Solo** hay cambios de lógica en los archivos autorizados.
- No hay reproducción automática inesperada de movimientos de manos ni frases por defecto.
- La micro-pausa ya no fuerza por sí sola cambios de grupo.
- No hay refundidos encadenados desde imágenes mezcladas.
- La elección automática de anclas comprueba compatibilidad real usando la información disponible, sin sobrecargar el render.
- No se pierden los 62 archivos ni la configuración del usuario y se conservan sus gestos manuales.
- **La modificación del código no prueba por sí sola la calidad visual**: el usuario realizará la evaluación en su equipo. Si las tomas originales son incompatibles, podría seguir habiendo transiciones perceptibles.

## 7. Respuesta FINAL obligatoria de Claude

Cuando termines **la edición del código**, envía una respuesta corta en español con este formato:

```text
CORRECCIÓN DE LÓGICA FINALIZADA

Archivos modificados:
- ruta: cambio puntual

Cambios aplicados:
1. Selección automática de habla: ...
2. Frases automáticas: ...
3. Micro-pausas: ...
4. Fundidos interrumpidos: ...
5. Anclas y fallback: ...

Pruebas ejecutadas: NINGUNA (por indicación del usuario).
Cambios NO realizados: interfaz, audios, vídeos, manifiesto, configuración del usuario,
instaladores, ejecutables, documentación y tests.
Pendientes o límites: ...

El usuario puede iniciar la aplicación desde el código fuente y realizar sus pruebas manuales.
```

**No anuncies que está terminado antes de concluir realmente los cambios. No prometas volver más tarde. No empieces una siguiente fase de optimización sin una nueva autorización del usuario.**
