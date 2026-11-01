# CAMVOICE — CORRECCIÓN CRÍTICA: EVITAR QUE EL AUDIO DE OTROS PARTICIPANTES ACTIVE EL VÍDEO DE HABLA

## Rol y objetivo

Actúa como ingeniero sénior especializado en **audio en tiempo real en Windows, WebRTC VAD, cancelación de eco acústico (AEC), WASAPI/PortAudio y concurrencia en Python**.

**Repositorio:** `D:\Git-Hub\camvoice`.

**Problema reproducido por el usuario:** durante reuniones de Teams u otra aplicación de videollamadas, cuando hablan los participantes remotos, el sonido sale por los altavoces y CamVoice interpreta esa señal como si hablara el usuario local. El vídeo entra erróneamente en `SPEAKING`. Cuando solo habla otra persona, debe mantenerse en `SILENT`.

**Resultado buscado:** que la decisión `SPEAKING` se base en **voz cercana del usuario local** y no en la reproducción de audio remoto que se filtra acústicamente al micrófono. Preservar la detección de su voz, incluso cuando otro participante está hablando (doble habla), en la medida en que los dispositivos y la señal lo permitan.

> **Muy importante:** seleccionar «solo el micrófono» NO resuelve el problema cuando el micrófono recibe físicamente el sonido de los altavoces. Tampoco basta con subir el umbral dBFS o SNR, ni con WebRTC VAD por sí solo: esos mecanismos reconocen habla, pero no quién habla. No presentes como garantía una separación imposible sin referencias o información suficiente.

## 1. Alcance estricto y protección del trabajo existente

- **Únicamente** intervenir la ruta de captura, referencia de reproducción, supresión/detección de eco y decisión de voz local en `camvoice/audio/` y, si fuera imprescindible, el mínimo código de conexión o configuración necesario.
- **No modificar:** `camvoice/video/director.py`, `camvoice/video/index.py`, compositor, transiciones, grupos de clips, manifest de biblioteca, las 62 grabaciones, gestos, atajos, instaladores o interfaz; tampoco modificar los parámetros actuales del vídeo.
- Mantener el uso **local, sin nube, sin GPU y sin transmisión ni almacenamiento de voz**. No grabar micrófono ni audio remoto en archivos de diagnóstico.
- Mantener la compatibilidad con el comportamiento del audio cuando no existe reproducción remota o cuando se usan auriculares.
- No refactorizar módulos ajenos ni introducir dependencias pesadas por conveniencia. Si una dependencia adicional es realmente imprescindible, justificar compatibilidad con Windows/Python 3.12, uso offline, licencia y posibilidad de empaquetado; **no instalarla automáticamente sin necesidad**.
- No hacer commits, no reconstruir `.exe`, no ejecutar benchmarks de horas ni pruebas de cámara. El usuario realizará la validación funcional. Admitir únicamente verificaciones estáticas ligeras de los archivos modificados.

## 2. Auditar la causa ANTES de implementar

Inspecciona el código actual y la configuración efectiva, sin asumir que sigue igual que en el informe anterior:

1. `camvoice/audio/capture.py`: dispositivo de entrada real, PortAudio, `sounddevice`, API de Windows, selección de micrófono, frecuencia de muestreo, canales, buffers y callbacks.
2. `camvoice/audio/vad.py`: cambios recientes de WebRTC VAD + umbral energético + SNR dinámico; determinar si un audio de altavoz que llega al micrófono supera correctamente esas condiciones.
3. `camvoice/audio/pipeline.py`, `gate.py` y `features.py`: cuándo se publica `VoiceStatus`, latencia de ataque/release y decisión final de voz.
4. Configuración de audio de CamVoice: confirmar que el dispositivo seleccionado no sea **Stereo Mix**, «What U Hear», loopback o una entrada virtual que ya incluya audio del sistema. No cambiar silenciosamente el dispositivo elegido por el usuario.
5. Verificar si ya existe algún mecanismo real de **AEC, referencia de salida del sistema, correlación de eco o control de doble habla**. Diferenciar código operativo de comentarios/documentación.
6. Distinguir tres causas posibles: (A) captura equivocada de audio del sistema, (B) fuga acústica desde altavoces al micrófono, (C) audio procedente de personas físicamente cercanas. Esta intervención se centra especialmente en A y B; no prometer resolver C sin otra tecnología.

Antes de tocar código, establece cuál de las causas explica mejor la arquitectura actual y qué dispositivos/servicios de Windows están realmente disponibles. No supongas que Windows aplica AEC automáticamente a todo stream de PortAudio.

## 3. Diseñar y aplicar una solución de varias capas

Evalúa explícitamente las alternativas siguientes y **aplica la combinación mínima viable y robusta**, en función de lo que soporte el proyecto:

### Capa A — Captura correcta

- Asegurar que la fuente de decisión es una entrada de micrófono físico/seleccionado, nunca el audio de salida capturado por error.
- Respetar el dispositivo configurado por el usuario y detectar cambios/desconexiones sin colgar el hilo de audio.
- No interpretar el valor RMS alto como prueba de que está hablando el usuario local.

### Capa B — Referencia del audio remoto y aislamiento de eco

- Investigar primero un mecanismo **AEC comprobablemente disponible**, preferentemente un procesador de comunicaciones/AEC local compatible con Windows y la arquitectura real. No inventar métodos o bindings inexistentes.
- Si es necesario y viable, obtener la señal de **salida reproducida** mediante WASAPI loopback como **referencia de eco, no como micrófono**. Esa referencia debe identificar audio que proviene del equipo, no decidir por sí sola que hay voz local.
- Comparar temporalmente referencia y micrófono usando búferes con marcas de tiempo, corrección razonable del retardo, remuestreo/canales si difieren y un estimador eficiente de semejanza/coherencia. Considerar latencia variable, dispositivos Bluetooth, cambio de altavoz y desbordes de buffer.
- Preferir cancelación de eco efectiva (si está soportada) con criterio de voz residual/cercana. **No descartar automáticamente toda trama de micrófono mientras exista sonido de salida**: impediría que el usuario hable simultáneamente a los demás.
- Si solo se puede implementar un clasificador de «probable eco» sin AEC real, documentarlo expresamente y hacerlo conservador: rechazar el evento cuando haya evidencia sólida de reproducción remota y **no haya evidencia suficiente de voz local residual**; preservar doble habla cuando sea distinguible. No afirmar precisión absoluta.
- No ejecutar correlaciones grandes, conversiones costosas o lecturas WASAPI bloqueantes en el callback de captura ni en el hilo de renderizado.

### Capa C — Decisión final de voz local

Construir o adaptar una decisión clara y medible, por ejemplo:

`near_end_speech = mic_vad AND energy_snr_ok AND NOT confirmed_echo_only`

Esta fórmula es ilustrativa; si el procesamiento de AEC permite decisión sobre el residual, utilizar criterios adecuados y separar estados «eco remoto probable», «habla local probable», «doble habla» e «indeterminado».

- Mantener una **histéresis temporal**, evitando cambios frecuentes por ruido o eco residual.
- Tratar silencio, pausas, reinicio de dispositivo y ausencia de referencia sin producir activaciones espurias.
- Evitar modificar sin justificación los valores `attack_ms`, `release_ms` y los ajustes VAD que ya fueron corregidos.
- Con incertidumbre alta, priorizar la no activación por habla remota, pero documentar la posibilidad de perder voz local tenue. No convertir ese criterio en bloqueo permanente ante cualquier reproducción del sistema.

### Capa D — Degradación segura y alternativa práctica

- Si no existe manera fiable de obtener la referencia o de ejecutar AEC en este entorno, **no fabricar un detector de identidad del hablante** ni declarar el problema resuelto.
- Conservar el funcionamiento anterior cuando no haya eco significativo, y señalar claramente la limitación con altavoces.
- Explicar que **usar auriculares y seleccionar un micrófono cercano** es la mitigación más fiable e inmediata cuando el problema proviene de fuga acústica.
- Si ni siquiera con referencia se puede separar la doble habla en condiciones reales, documentar esa limitación y proponer control manual opcional para un trabajo posterior, **sin añadir botones ni UI en este encargo**.

## 4. Casos que la implementación debe contemplar

| Escenario | Resultado esperado |
|---|---|
| Habla un participante remoto por los altavoces; usuario local callado | `SILENT`; no activar clips de habla por eco |
| Participante remoto sigue hablando y el usuario empieza a responder | Reconocer voz local si la señal permite separarla; evitar veto ciego por audio remoto |
| Usuario habla solo; no hay reproducción remota | `SPEAKING` normalmente, sin latencia excesiva |
| Participante remoto calla y queda eco/reverberación residual | No activar `SPEAKING` espuriamente |
| Golpe de mesa, teclado, ventilador, ruido breve | No activar `SPEAKING` por ruido aislado |
| Auriculares con reproducción remota y micrófono cercano | Comportamiento normal, sin dependencias innecesarias del loopback |
| Cambia el micrófono, dispositivo de salida o la latencia | Reinicialización segura, sin bloqueos ni decisiones basadas en referencia obsoleta |
| No hay permisos/dispositivo loopback/AEC disponible | Manejo explícito, no bloquear el render ni ocultar el límite |

## 5. Rendimiento, integridad y observabilidad

- Conservar captura de baja latencia y sincronización estable de buffers; no añadir decodificación de vídeo ni tocar el render.
- Usar procesamiento acotado y no bloqueante, sin cargar modelos gigantes ni invocar servicios externos.
- Incluir contadores/diagnóstico **opt-in** (por consola o logging existente, sin cambios de UI) suficientes para entender: dispositivo de entrada y salida, backend VAD, si AEC/referencia está disponible, estado de eco probable y motivo de aceptación/rechazo de voz. No registrar audio en bruto ni información de reuniones.
- Evitar que una excepción del hilo de referencia cierre CamVoice o cambie arbitrariamente a `SPEAKING`.
- No degradar el caso «solo habla el usuario» únicamente por maximizar rechazo de voz remota.

## 6. Condiciones de finalización

1. Cambiar **solo** los archivos indispensables del subsistema de audio (más configuración estrictamente necesaria, si aplica).
2. Revisar los diffs y sintaxis sin modificar otros módulos. **No ejecutar `pytest`, soak, cámaras ni pruebas largas**: las hará el usuario.
3. No declarar éxito medido sin haber hecho pruebas reales. Separar claramente lo implementado de lo no verificado.
4. Al terminar, informar concisamente:
   - **causa raíz** hallada y evidencia (`archivo:línea`);
   - **estrategia elegida** y por qué se descartan otras;
   - **archivos modificados** y comportamiento previsto;
   - **limitaciones** (sobre todo altavoces, AEC, doble habla y dispositivos no compatibles);
   - **pasos breves de prueba manual**, sin cambios adicionales.
5. Finalizar con: **«CORRECCIÓN DE AUDIO FINALIZADA — PENDIENTE DE VALIDACIÓN MANUAL DEL USUARIO»**, si efectivamente está implementada. Si no es viable de forma fiable con la arquitectura y dependencias existentes, indicarlo de manera transparente con el impedimento técnico preciso.

## 7. Restricción principal

**No quiero una simple subida de umbrales ni otra capa RMS que siga confundiendo la voz de los participantes con la mía. Quiero un análisis real de cómo llega el audio remoto al micrófono y una implementación defensiva basada en la arquitectura existente, con especial cuidado de no romper el resto de CamVoice.**
