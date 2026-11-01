# PROMPT MAESTRO — CAMVOICE: TRANSICIONES NATURALES, INTERFAZ INTUITIVA E IMPORTACIÓN DE 62 GRABACIONES

> **Destinatario:** Claude Code, ejecutándose en el equipo Windows del usuario, con acceso real al repositorio y a las grabaciones locales.  
> **Modalidad:** implementar y comprobar código, **no** quedarse en un análisis o en documentos de diseño.  
> **Repositorio:** `D:\Git-Hub\camvoice`  
> **Origen de grabaciones:** `C:\Users\USUARIO\Pictures\Camera Roll`  
> **Idioma de interfaz y documentación:** español.  
> **Restricciones:** Windows, funcionamiento local/offline, sin GPU obligatoria, sin IA generativa ni servicios externos; compatibilidad con el proyecto existente.

## 0. Objetivo y definición de éxito

Implementa, sobre el repositorio **real y actual**, tres mejoras integradas:

1. **Continuidad visual:** reducir todo lo posible saltos de postura, cambios de encuadre/luz, doble imagen (*ghosting*), tirones, repetición mecánica y transiciones bruscas al alternar `SILENT`, `SPEAKING` y `GESTURE`. Prioriza la **continuidad de la cabeza y el fondo** por encima de una supuesta sincronización labial que los clips pregrabados no pueden proporcionar.
2. **Experiencia de usuario:** que una persona pueda abrir CamVoice, importar sus vídeos, comprobar calidad, preparar caché, previsualizar, asignar gestos y habilitar la cámara virtual sin leer instrucciones técnicas ni enfrentarse a una pantalla recargada.
3. **Importación fiel:** localizar e incorporar las **62 grabaciones existentes**, cuyos archivos tienen por nombre el número de la escena según la tabla de este documento. No inventar ni exigir los vídeos **50, 52, 59 y 63**, que NO fueron grabados.

No consideres “terminado” el trabajo porque el programa arranque o porque pase `pytest`: debe existir un flujo de uso funcional y evidencia de continuidad mejorada, con límites reales comunicados honestamente. La imagen y la interfaz deben conservar indicaciones de que se utiliza vídeo pregrabado; no diseñar mecanismos para fingir problemas de red o hacer pasar el vídeo por una imagen en directo.

## 1. Inspección obligatoria antes de tocar el código

Desde el repositorio:

```powershell
Set-Location 'D:\Git-Hub\camvoice'
git status --short
git branch --show-current
Get-ChildItem -Force
.\.venv\Scripts\python.exe -m camvoice doctor
```

Luego, inspecciona con detenimiento el código y las pruebas existentes; confirma rutas y nombres antes de usarlos. Presta especial atención a los módulos reales responsables de:

- `SpeechGate`, VAD/histéresis, composición, reproducción, biblioteca de vídeos, manifest, índice/cache, gestos, máquina de estados y cámara virtual.
- PySide6 (pestañas existentes: grabación, vídeos/biblioteca, gestos, salida, efectos, etc.), tray icon, teclas globales, validaciones y persistencia.
- La ruta de datos y configuración por usuario; instalador portable y ejecutable compilado.
- Documentos `README.md`, `docs/DISENO_CAMVOICE_TIEMPO_REAL.md`, `docs/GUIA_GRABACION_Y_GESTOS.md`, `docs/VALIDACION_CAMVOICE.md` y `docs/PRUEBAS.md`, si existen.

**El README conocido describe** un “modo director” por defecto con clips continuos, fotogramas ancla (espera máxima aprox. 180 ms; fundido alternativo de unos 200 ms), variantes de gestos, pausa con último fotograma, `playback: classic` como compatibilidad, caché `memmap` NV12, interfaz en PySide6 y comandos `python -m camvoice index`, `gestures`, `soak` y `verify-camera`. **Verifica todo eso en el código actual:** la documentación puede no coincidir con la implementación. No dupliques subsistemas que ya existan; refactoriza con cambios pequeños y probables.

No borres cambios locales del usuario; no hagas `git reset --hard`, `git clean -fd`, migraciones destructivas, borrado de fuentes ni commits automáticos. Registra el estado inicial y los resultados de las pruebas de referencia.

## 2. Transiciones de calidad de producción (PRIORIDAD MÁXIMA)

### 2.1 Analizar y preparar cada clip UNA SOLA VEZ, fuera del render

Construye o amplía el indexador offline para calcular y guardar **metadatos por clip**:

- Resolución, FPS y duración reales; orientación/rotación; formato; integridad y capacidad de decodificación; advertencias de clips corruptos, excesivamente cortos o con FPS variable.
- Características baratas y reproducibles: posición/escala de cabeza, orientación general, estimación de boca cerrada/abierta, pose neutra, similitud de fondo, exposición/temperatura/color y calidad de fotogramas.
- **Múltiples fotogramas ancla candidatos** por clip, con índices, puntaje de calidad y límites de confianza; posibilidad de marcar/ajustar anclas manualmente desde la interfaz cuando el detector no sea fiable.
- Para los clips de habla, segmentos **verdaderamente continuos** de longitud orientativa 1,0–3,5 s entre puntos razonables; no cortes arbitrariamente si no hay anclas adecuadas. Para los de escucha, detectar lugares de empalme sin saltos notorios.
- Índice persistente versionado que pueda regenerarse solo para los clips cambiados. No volver a procesar 62 vídeos completos en cada inicio.

Usa las dependencias ya disponibles si son suficientes. Si precisas una nueva, justifica y controla tamaño/licencia, rendimiento y modo offline. No impongas modelos generativos, descargas automáticas o GPU.

### 2.2 Elegir un destino visualmente compatible

- Reproducir los fotogramas **en orden secuencial**, sin seleccionar un fotograma diferente cada frame según el RMS/energía vocal: eso crea vibración en cabeza, ojos y fondo.
- Escoger próximos **segmentos o anclas** a partir de una lista pequeña de candidatos ya calculada; minimizar diferencias de posición de cabeza, escala, dirección de mirada, boca, brillo y fondo.
- Mantener variedad entre clips y evitar repetir siempre el mismo primer fotograma; introducir diversidad solo en los **puntos de empalme válidos**, no por saltos arbitrarios.
- Evitar el cambio de dirección visible del modo `pingpong`: preferir cierres de bucle en anclas compatibles y conservar `classic` sin romperlo.
- No afirmar “sincronización labial”: el objetivo es coherencia de estados y pausas, no fonemas alineados.

### 2.3 Hacer transiciones con restricciones temporales REALISTAS

Al pedir cambio `SPEAKING ↔ SILENT`, gesto, renovación de segmento o salida de pausa:

1. Mantén el clip de origen reproduciéndose secuencialmente mientras buscas el siguiente ancla preindexada apropiada.
2. Permite **máximo configurable 150–200 ms** de espera desde la solicitud (preferencia inicial: 180 ms). Usa un **límite por tiempo**, NO “1–4 fotogramas” porque a 15 FPS cuatro fotogramas suponen ~267 ms.
3. Escoge ancla de destino de apariencia más próxima, sin búsquedas de todos los fotogramas en el hilo de render.
4. En anclas muy compatibles, fundido breve y estable, con duración ajustada a la cantidad real de fotogramas: a 15 FPS 80 ms son apenas 1–2 frames. Prioriza continuidad y verifica el resultado visual antes de fijar un valor (rangos de partida: 100–200 ms, configurables).
5. **Fallback obligatorio** al agotarse la espera: fundido controlado entre fotogramas disponibles, sin bloqueo, imagen negra ni congelamientos inesperados. Registrar un contador de transiciones con fallback.
6. No aplicar deformaciones de rostro por flujo óptico (Farneback/DIS, warping facial) por defecto: pueden distorsionar ojos, boca y pelo. Si hay código heredado de esto, dejarlo desactivado de forma segura.
7. Evita mezclar dos rostros desplazados con alpha puro por defecto. Usa selección de pares realmente compatibles y, si se justifica, alineación global conservadora o transición espacial limitada; prohibido deformar ojos/boca. Comprueba que la ganancia sea perceptible y no aparezcan bordes, halos ni dobles caras.

Define una función de coste/umbral de discontinuidad y una prueba cuantitativa con las grabaciones reales, dejando claro qué métricas son aproximaciones perceptuales y cuáles son medidas objetivas.

### 2.4 Uniformidad óptica sin artificializar el rostro

- Normaliza de manera conservadora brillo, balance de blancos/temperatura y contraste **por clip** respecto a un clip de referencia (preferentemente el N.º 1), evitando parpadeos y sobreprocesado de piel.
- Corrige diferencias pequeñas de recorte, escala y posición del **encuadre completo** si está justificado; no realizar warping agresivo del rostro.
- Valida que el fondo no “salte” en el empalme. Evita segmentación inestable o cambiar a un fondo sintético si no hay beneficio comprobado.
- Guarda versiones/cache de trabajo sin alterar los MP4 originales. Permite reconstrucción/rollback de la preparación.

### 2.5 SpeechGate, gestos y pausa

- Estados explícitos, deterministas y probables: `SILENT`, `SPEAKING`, `GESTURE`, `TRANSITION` y `VIDEO_PAUSED`; prioridades, preempciones, peticiones repetidas y reglas de cancelación definidas en pruebas.
- Ajustes por defecto **propuestos** (calibrar, no imponer): `release_ms` 150–250 ms, `min_speaking_ms` 180–220 ms; histéresis para evitar alternancia nerviosa y falsas activaciones. Verificar captura solo del micrófono seleccionado; considerar fugas de audio de altavoces.
- Mantener separadas detección de voz, planificador de segmentos, selección de gestos y compositor. No acceder a disco, decodificar o buscar globalmente dentro del bucle por fotograma.
- Variantes opcionales de gesto `<nombre>_silencio` / `<nombre>_hablando` cuando existan de verdad. No atribuir una variante “hablando” a un archivo de gesto silencioso sin evidencia. Si no existe y el gesto se solicita mientras se habla, esperar hasta 200 ms por micro-pausa cuando sea apropiado y luego ejecutar una política determinista (sin bloqueo indefinido).
- Mantener pausa manual `VIDEO_PAUSED`: último fotograma válido, estado bien señalizado **en la interfaz**, reanudación mediante transición razonable. Nunca pantalla negra por error de transición.
- Reacciones automáticas solo cuando sean opcionales, discretas y no interfieran con gestos manuales.

### 2.6 Rendimiento y robustez

- Respetar presupuesto de frame a 15 FPS (66,7 ms); no fijar 30 FPS por defecto sin probarlo en hardware objetivo. Si se habilita, comprobar CPU/RAM/fluidez.
- Índices precalculados, `memmap`/caché existentes, colas acotadas, sin bloqueo en audio ni render, sin fugas de memoria ni acumulación de frames.
- Instrumentar FPS efectivo, duración p50/p95/p99 de composición/transición, frames descartados, espera hasta gesto, uso aproximado CPU/RAM, transiciones por fallback y métricas comparables antes/después.
- Asegurar cierre ordenado/reinicio y funcionamiento continuo de 1–2 horas.

## 3. Interfaz gráfica: simple para el usuario final

**No rehagas toda la interfaz sin necesidad.** Adapta PySide6 y sus pantallas actuales para lograr un flujo claro:

1. **Biblioteca:** “Seleccionar carpeta”, ruta origen autocompletada, botón “Detectar 62 grabaciones”, cuadrícula/lista de vídeos con número, acción, tipo (`silencio`, `habla`, `gesto`), duración, miniatura y estado (`Listo`, `Por revisar`, `Error`); filtros por tipo y buscador. Mostrar **62/62 importadas**, más cuatro omitidas intencionalmente; nunca denunciar como error obligatorio las cuatro descartadas.
2. **Preparar:** acción primaria “Validar y preparar vídeos”, barra de progreso real con fase/número de clip, tiempo transcurrido, botón cancelar y reanudar, informe legible de avisos de encuadre, audio, iluminación, baja confianza de anclas, etc. No atribuir éxito si un paso falla.
3. **Vista previa:** seleccionar escena, reproducir su clip, probar “hablar → callar”, “callar → hablar” y “gesto → silencio/habla”, mostrar par origen/destino, tiempo de espera y si hubo fallback; opciones simples de calibración, sin exponer parámetros avanzados innecesariamente.
4. **Cámara virtual:** indicador inequívoco de dispositivo instalado, permiso/configuración y flujo en ejecución; botones grandes “Iniciar vista previa”, “Iniciar cámara virtual”, “Detener”. Mostrar el estado actual y el aviso **VÍDEO PREGRABADO** en la interfaz; conservar la posibilidad de identificación en la salida de vídeo existente.
5. **Gestos:** galería con miniatura, descripción y atajo; probar, cancelar, reasignar teclas sin colisiones, y señalar cuando no exista variante hablada. Mantener atajos y bandeja actuales y ofrecer ayuda contextual breve.

Buenas prácticas de interfaz: lenguaje claro en español, iconos con texto, feedback real de errores, contraste y navegación accesibles, estados vacíos útiles, confirmación antes de reemplazar configuración, sin modales innecesarios, sin congelar UI, previsualizaciones fluidas y diseño sobrio. Mantener opciones avanzadas en un panel separado y conservar las funciones existentes (grabación, efectos, salida, etc.).

## 4. Importación REAL de las grabaciones del equipo

La ruta existente es exactamente:

```text
C:\Users\USUARIO\Pictures\Camera Roll
```

**Regla innegociable:** los ficheros se nombraron con el **número original de la escena**, y NO se deben renumerar según el orden de importación. No se subieron los 62 vídeos a esta conversación: tienes que encontrarlos en el disco Windows al ejecutar este prompt.

### 4.1 Detectar de forma robusta

- Buscar en la carpeta indicada **solo archivos de vídeo** (`.mp4`, `.mov`, `.mkv`, `.avi`, según soporte real del programa). Por defecto, lectura de la carpeta indicada; subcarpetas solo si el usuario las habilita.
- Identificar nombres base numéricos (`1`, `01`, `001`, etc.), sin extensión visible en el Explorador; tomar el nombre real del archivo. No hacer coincidencias parciales como `V_aud_001` o `movimientos.mp4`.
- Excluir exactamente **50, 52, 59 y 63**; se descartaron de forma intencional. No crear archivos vacíos, inventar fotogramas ni asignar escenas sustitutas.
- Ignorar, salvo selección explícita, elementos no numerados como `foto`, `movimientos.mp4`, `V_aud_001`, `V_aud_002` y cualquier contenido ajeno a la biblioteca.
- Si hay dos vídeos con el mismo número y distinta extensión, **detener solo esa entrada y pedir resolver la ambigüedad**, sin escoger al azar; el resto puede seguir importándose.
- No asumir que los 62 vídeos estén necesariamente sanos: comprobar decodificación, metadatos, tamaño, duración y que la escena visual coincida razonablemente con su etiqueta; la correspondencia semántica final puede requerir revisión humana.

### 4.2 Manifest, almacenamiento y seguridad de datos

- Adaptarte al **formato de manifest y estructura reales** del proyecto. Versionar y migrar conservadoramente. No crear otro manifest incompatible si hay uno existente.
- El manifest debe conservar para cada entrada: `source_number` original, etiqueta/slug, categoría, ruta origen, ruta importada o de trabajo, duración/FPS/resolución comprobados, índice/cache, anclas, estado de validación y opciones de reproducción pertinentes. Son campos **conceptuales**: ajustar los nombres exactos a la arquitectura existente.
- Categorías de la tabla: `SILENT_BASE`, `SILENT_VARIATION`, `SPEAKING_BASE`, `SPEAKING_VARIATION`, `SPEAKING_ONE_SHOT` (no hacer bucle de respuestas breves) y `GESTURE` (una vez, retornar al estado actual).
- Copiar a biblioteca de usuario o referenciar fuentes de forma segura según cómo funciona CamVoice; para una biblioteca autónoma preferir copia/ingesta que **no altere originales**. Mostrar espacio requerido y ruta de destino antes de copiar lotes grandes; hacer copia/configuración atómica y permitir cancelar/reintentar.
- Los vídeos personales deben permanecer locales: no subirlos a servidores, no hacer commit de ellos ni de sus miniaturas/cache sensibles. Revisar `.gitignore` y logs. No ejecutar ningún borrado de originales.
- Importación **idempotente**: segunda ejecución no duplica escenas ni reprocesa material idéntico; conservar asignaciones manuales, hotkeys, efectos y configuraciones existentes.

### 4.3 Mapeo exacto N.º original → acción

**Esta tabla es la fuente de verdad para asignar nombres y categorías.** Respeta cada número. El slug es sugerido; si el esquema actual tiene nombres distintos, usa su convención sin perder la correspondencia.

| N.º archivo | Acción / escena | Categoría | Slug sugerido | Indicador visual / descripción |
|---:|---|---|---|---|
| **1** | Silencio neutro | `SILENT_BASE` | `silencio_neutro` | Mirar a cámara, respirar y parpadear con naturalidad; boca cerrada. |
| **2** | Habla natural | `SPEAKING_BASE` | `habla_natural` | Hablar de manera natural, mirando al objetivo y sin gestos exagerados. |
| **3** | Escucha atenta | `SILENT_VARIATION` | `escucha_atenta` | Escuchar con movimientos pequeños de ojos/cabeza. |
| **4** | Hablar sin mover las manos | `SPEAKING_VARIATION` | `hablar_sin_manos` | Hablar con manos apoyadas y mirada a cámara. |
| **5** | Hablar con movimientos de cabeza | `SPEAKING_VARIATION` | `hablar_movimientos_cabeza` | Hablar con inclinaciones discretas de cabeza. |
| **6** | Terminar una explicación | `SPEAKING_ONE_SHOT` | `terminar_explicacion` | Finalizar frase, cerrar boca y quedar en postura neutra. |
| **7** | Respuesta corta | `SPEAKING_ONE_SHOT` | `respuesta_corta` | Pronunciar sí, claro o vale y volver a reposo. |
| **8** | Frase corta | `SPEAKING_ONE_SHOT` | `frase_corta` | Decir una frase breve y volver a expresión neutra. |
| **9** | Asentir una vez | `GESTURE` | `asentir_una_vez` | Asentir suavemente una vez y volver al centro. |
| **10** | Asentir varias veces | `GESTURE` | `asentir_varias_veces` | Asentir dos o tres veces y volver al centro. |
| **11** | Sonrisa breve | `GESTURE` | `sonrisa_breve` | Sonreír un instante y relajar la cara. |
| **12** | Negar con la cabeza | `GESTURE` | `negar_cabeza` | Negar de forma suave y regresar al frente. |
| **13** | Sorpresa leve | `GESTURE` | `sorpresa_leve` | Levantar ligeramente las cejas y relajar. |
| **14** | Mirar abajo y volver | `GESTURE` | `mirar_abajo_volver` | Mirar hacia abajo y volver a cámara. |
| **15** | Mirar a otra pantalla | `GESTURE` | `mirar_otra_pantalla` | Mirar hacia un costado y regresar. |
| **16** | Mirar hacia arriba | `GESTURE` | `mirar_arriba` | Elevar la mirada y volver. |
| **17** | Saludar con la mano | `GESTURE` | `saludar_mano` | Levantar mano, saludar y bajar. |
| **18** | Despedirse con la mano | `GESTURE` | `despedirse_mano` | Despedirse con una mano y volver. |
| **19** | Hablar sonriendo | `SPEAKING_VARIATION` | `hablar_sonriendo` | Hablar con sonrisa moderada. |
| **20** | Hablar explicando | `SPEAKING_VARIATION` | `hablar_explicando` | Hablar explicando, con movimientos leves de cabeza y sin manos. |
| **21** | Hablar seriamente | `SPEAKING_VARIATION` | `hablar_serio` | Hablar con expresión concentrada. |
| **22** | Hablar relajadamente | `SPEAKING_VARIATION` | `hablar_relajado` | Hablar con expresión amistosa y relajada. |
| **23** | Sonreír y asentir | `GESTURE` | `sonreir_asentir` | Sonreír y asentir una vez. |
| **24** | Señal de un momento | `GESTURE` | `senal_un_momento` | Palma al frente brevemente y bajar la mano. |
| **25** | Mirar al teclado | `GESTURE` | `mirar_teclado` | Bajar la vista al teclado y volver. |
| **26** | Acercarse a la pantalla | `GESTURE` | `acercarse_pantalla` | Inclinar torso hacia delante y volver. |
| **27** | Pedir la palabra | `GESTURE` | `pedir_palabra` | Levantar mano y volver. |
| **28** | Expresión pensativa | `GESTURE` | `expresion_pensativa` | Expresión de concentración breve. |
| **29** | Reír ligeramente | `GESTURE` | `reir_ligeramente` | Risa breve y discreta y volver. |
| **30** | Mirar a la izquierda | `GESTURE` | `mirar_izquierda` | Desviar mirada a izquierda y volver. |
| **31** | Inclinar cabeza a la izquierda | `GESTURE` | `inclinar_cabeza_izquierda` | Inclinar suavemente cabeza y volver. |
| **32** | Inclinar cabeza a la derecha | `GESTURE` | `inclinar_cabeza_derecha` | Inclinar suavemente cabeza y volver. |
| **33** | Cejas de comprensión | `GESTURE` | `cejas_comprension` | Levantar cejas brevemente. |
| **34** | Fruncir el ceño | `GESTURE` | `fruncir_ceno` | Fruncir discretamente y relajar. |
| **35** | Mirar a la derecha | `GESTURE` | `mirar_derecha` | Mirar a la derecha y volver. |
| **36** | Entrecerrar los ojos | `GESTURE` | `entrecerrar_ojos` | Entrecerrar ojos brevemente y relajar. |
| **37** | Pensar con mano en mentón | `GESTURE` | `pensar_mano_menton` | Llevar mano al mentón y retirarla. |
| **38** | Hablar con una mano | `SPEAKING_VARIATION` | `hablar_una_mano` | Hablar gesticulando discretamente con una mano. |
| **39** | Hablar con ambas manos | `SPEAKING_VARIATION` | `hablar_ambas_manos` | Hablar con gestos moderados de ambas manos. |
| **40** | Hablar consultando notas | `SPEAKING_VARIATION` | `hablar_consultando_notas` | Hablar, mirar abajo y regresar. |
| **41** | Hablar enfatizando | `SPEAKING_VARIATION` | `hablar_enfatizando` | Hablar elevando ligeramente las cejas. |
| **42** | Levantar el índice | `GESTURE` | `levantar_indice` | Levantar un dedo índice y bajarlo. |
| **43** | Mostrar dos dedos | `GESTURE` | `mostrar_dos_dedos` | Mostrar dos dedos y bajar. |
| **44** | Mostrar tres dedos | `GESTURE` | `mostrar_tres_dedos` | Mostrar tres dedos y bajar. |
| **45** | Señal de OK | `GESTURE` | `senal_ok` | Hacer señal OK con la mano y volver. |
| **46** | Ambas palmas abiertas | `GESTURE` | `palmas_abiertas` | Levantar ambas palmas y bajar. |
| **47** | Señalar hacia un lado | `GESTURE` | `senalar_lado` | Señalar a un costado y volver. |
| **48** | Apoyar las manos | `GESTURE` | `apoyar_manos` | Apoyar manos en la mesa y quedar neutro. |
| **49** | Escribir una nota | `GESTURE` | `escribir_nota` | Mirar abajo, escribir brevemente y volver. |
| **51** | Acomodar la camisa | `GESTURE` | `acomodar_camisa` | Acomodar cuello de camisa y volver. |
| **53** | Inclinarse hacia adelante | `GESTURE` | `inclinarse_adelante` | Inclinar torso adelante y volver. |
| **54** | Respirar y relajarse | `GESTURE` | `respirar_relajarse` | Respirar suavemente y relajar hombros. |
| **55** | Pulgar arriba | `GESTURE` | `pulgar_arriba` | Mostrar pulgar arriba y bajar. |
| **56** | Moverse a la izquierda | `GESTURE` | `moverse_izquierda` | Acomodarse a la izquierda en la silla y volver. |
| **57** | Moverse a la derecha | `GESTURE` | `moverse_derecha` | Acomodarse a la derecha en la silla y volver. |
| **58** | Echarse hacia atrás | `GESTURE` | `echarse_atras` | Echarse algo hacia atrás/estirarse y regresar. |
| **60** | Ajustar gafas o pelo | `GESTURE` | `ajustar_gafas_o_pelo` | Ajustarse gafas o pelo si corresponde. |
| **61** | Tocarse la cara | `GESTURE` | `tocarse_cara` | Tocar brevemente la cara y bajar mano. |
| **62** | Acomodar el cabello | `GESTURE` | `acomodar_cabello` | Acomodar cabello y volver. |
| **64** | Tocarse la oreja | `GESTURE` | `tocarse_oreja` | Tocar oreja brevemente y volver. |
| **65** | Cruzar los brazos | `GESTURE` | `cruzar_brazos` | Cruzar brazos por un momento y regresar. |
| **66** | Apoyar el mentón | `GESTURE` | `apoyar_menton` | Apoyar mentón en mano y retirarla. |


### 4.4 Escenas intencionalmente NO grabadas

| N.º | Acción descartada | Tratamiento requerido |
|---:|---|---|
| **50** | Acomodar auriculares | Omitir sin error |
| **52** | Mirar hacia la puerta | Omitir sin error |
| **59** | Beber agua | Omitir sin error |
| **63** | Ajustar los lentes | Omitir sin error |

**Comprobación aritmética:** 66 escenas planificadas − 4 descartadas = **62 vídeos esperados**. La numeración restante contiene huecos **deliberados**. El archivo `58` **sí existe** (echarse hacia atrás); el `59` no existe porque beber agua no fue grabado. No intercambiarlos.

### 4.5 Pautas para el comportamiento de cada categoría

- `SILENT_BASE` (#1) = base de escucha; `SILENT_VARIATION` (#3) = alternativa de escucha, no gesto interminable.
- `SPEAKING_BASE` (#2) y `SPEAKING_VARIATION` (#4, #5, #19–22, #38–41) = material de voz; reproducir secuencialmente / por segmentos continuos compatibles, no hacer saltos sueltos ni sobreutilizar siempre el mismo clip.
- `SPEAKING_ONE_SHOT` (#6–8) = respuestas o cierres breves: no ponerlas en bucle continuo; tratar como acciones específicas o recursos para segmentos si la arquitectura lo permite sin saltos.
- `GESTURE` = acción bajo demanda, ejecutada una vez, con transición de salida; no reproducir todos los gestos indiscriminadamente ni asignar 62 atajos globales.
- Los clips gestuales NO son automáticamente versiones `_hablando`. Si el usuario luego graba una variante explícita, ofrecer asociarla sin romper este mapeo numérico.

## 5. Validación automatizada, visual y de larga duración

### 5.1 Antes/después medible

Genera una comparación **reproducible** entre implementación anterior y nueva, usando archivos permitidos y tests/mediciones en el equipo:

- Discontinuidad en rostro/posición/crop/fondo entre frames adyacentes en los cambios de estado; normaliza por FPS y movimiento real (no confiar exclusivamente en la diferencia absoluta de píxeles).
- Frecuencia y magnitud de `ghosting`, cambios abruptos de iluminación y de rostro, empalmes con ancla y fallbacks, frames negros, frames duplicados no intencionales.
- Latencia real de habla, silencio y gestos; FPS, CPU, RAM, p95/p99 por frame y recursos durante una sesión prolongada.
- Muestreo/revisión visual de una docena de transiciones entre clips **distintos**, varias veces cada una, incluyendo los peores casos y alternativas sin ancla.

Si no puedes ver en persona el resultado o no hay acceso a cámara/micrófono, marca esa parte **pendiente de validación manual**; no inventes una aprobación visual o medición.

### 5.2 Pruebas obligatorias

Cubre, al menos:

1. Detección de nombres `1.mp4`, `01.mp4` y huecos; 62 escenas, exactamente cuatro descartes; rechazo de duplicados/extraños, archivos corruptos y vídeos faltantes; manifest correcto por número, idempotencia, rollback y no borrado de fuentes.
2. Anclas fiables/no fiables, espera ≤ 200 ms, selección de candidato, fallback, segmentación secuencial sin saltos, límites en 15/30 FPS, control de iluminación y no-ghosting en casos sintéticos controlados.
3. Respuestas cortas y habla continua; micro-pausas; gestos durante habla y silencio; prioridades/cancelación; solicitudes simultáneas, pausas/reanudación, estrés al alternar rápidamente.
4. Compatibilidad con `playback: classic`, configuraciones/gestos antiguos, CLI, UI y instalador portable.
5. Que UI no se congele durante validación/indexación y muestre errores recuperables.
6. Simulación sin cámara, si el proyecto la contempla, durante 7200 s virtuales; y **prueba de resistencia real de hasta 2 horas** con observación de CPU/RAM/FPS cuando el hardware y el tiempo de ejecución lo permitan. Distinguir claramente tiempo simulado de tiempo real; no anunciar una prueba real si no fue realizada.

### 5.3 Ejecutar comandos y corregir fallos

Usa los comandos existentes **según lo que realmente soporta `--help`**. Por ejemplo:

```powershell
Set-Location 'D:\Git-Hub\camvoice'
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m camvoice doctor
.\.venv\Scripts\python.exe -m camvoice index
.\.venv\Scripts\python.exe -m camvoice gestures
.\.venv\Scripts\python.exe -m camvoice soak --simulate --duration 7200
.\.venv\Scripts\python.exe -m camvoice
```

No ejecutes a ciegas todos los comandos si `--help` o el código real indican que tienen otros argumentos. **No modifiques tests solo para obtener verde**; actualízalos únicamente cuando el comportamiento requerido cambie de forma justificada. Si reconstruir el portable es parte del flujo, compila con la ruta/script realmente existente y verifica que la UI del `.exe` refleje el código nuevo; evita lanzar una compilación vieja como si fuera la nueva.

## 6. Orden obligatorio de implementación

1. Inventario de código/configuración y situación de tests; diagnóstico del manifest actual.
2. Detectar, mapear e importar **62/62**, con vista previa, validación y exclusión intencional de los cuatro huecos.
3. Indexación de anclas, alineación fotométrica conservadora y segmentos secuenciales.
4. Planificador determinista de transiciones + fallback + pausa y gestos.
5. Renovar la interfaz y flujo de usuario sin eliminar funcionalidades existentes.
6. Añadir tests, correr métricas y corregir regresiones; comprobar ejecución desde código fuente y, si corresponde, desde portable.
7. Actualizar documentación estrictamente necesaria; elaborar resumen final.

**No preguntes por decisiones menores** si la solución segura se infiere del código, pero evita asumir que un vídeo inexistente existe y no elijas entre duplicados ambivalentes sin avisar. Puedes implementar una vía de resolución en UI sin detener todo el proyecto.

## 7. Criterios de aceptación verificables

- [ ] Los 62 clips con números correctos se detectan y asignan correctamente en un manifest compatible; **50/52/59/63 no se solicitan**.
- [ ] Importación segura, cancelable, idempotente y sin tocar material original.
- [ ] La interfaz permite encontrar, preparar, revisar, probar, asignar gestos e iniciar la cámara virtual sin terminal.
- [ ] Transiciones de estado mantienen continuidad de movimiento, sin jitter generado por salto de fotogramas; anclas seleccionadas offline y fallback sin bloqueo.
- [ ] No hay ojos/boca deformados por optical flow y se reducen los saltos de luz/encuadre respecto a la referencia, con pruebas/evidencias.
- [ ] Pérdidas de fotogramas, FPS, latencia, CPU/RAM y fallbacks medidos; limitaciones descritas sin promesas infundadas.
- [ ] `VIDEO_PAUSED`, hotkeys y `classic` continúan funcionando cuando existían.
- [ ] Suites previas y nuevas pasan, o se exponen con transparencia fallos y motivos.
- [ ] Los clips personales no se guardan en Git ni salen del PC.
- [ ] Se conserva una indicación clara de **VÍDEO PREGRABADO** en la interfaz y la opción existente de identificarlo en la salida.

## 8. Formato final exigido a Claude Code

Una vez ejecutado el trabajo, responde **breve y verificablemente**, con:

1. **Qué se implementó:** motor de transiciones, interfaz y biblioteca/importación.
2. **Archivos de código realmente modificados**, agrupados por funcionalidad.
3. **Importación:** `62/62` solo si se verificó en disco, con carpeta destino/manifest; incidencias por número si las hubo.
4. **Tests:** comando, cantidades aprobadas/fallidas/omitidas y pruebas manuales/soak reales o simuladas claramente diferenciadas.
5. **Métricas antes/después** con hardware y método, sin inventar cifras.
6. **Cómo iniciar la versión actualizada**: comando desde `.venv` y ubicación del ejecutable recompilado solo si se construyó realmente.
7. **Riesgos o tareas pendientes** (p. ej. marcación manual de anclas, revisión visual de algún clip, cámara real).

**No termines entregando únicamente un documento de diseño. Inspecciona, modifica, ejecuta, prueba y comunica lo que realmente se haya completado.**
