# CAMVOICE — SINCRONIZAR HABLA CON TEAMS Y CONTROL MANUAL DE SEGURIDAD

## Objetivo único

Trabaja **directamente** sobre el repositorio `D:\Git-Hub\camvoice` (Windows 11, Python 3.12, PySide6). Implementa **dos mecanismos complementarios** para impedir que CamVoice reproduzca un clip de habla cuando converso en privado y el micrófono de una reunión de **Microsoft Teams** está silenciado:

1. **Opción A — Detección automática del mute de Teams (preferente).** Leer el estado real y actual del micrófono de la reunión; nunca deducirlo del volumen de entrada.
2. **Opción B — Autorización manual de habla (respaldo).** Un control claro en la interfaz y un atajo configurable para autorizar o bloquear el estado de habla de CamVoice, independientemente de que siga recibiendo audio físico.

**Regla innegociable:** `SPEAKING` solo puede ocurrir con voz local confirmada **y** autorización efectiva de habla. `Teams = MUTED` o `sin reunión` debe vetar `SPEAKING` incluso si la persona habla junto al micrófono. No modificar el audio que reciben otros participantes ni controlar silenciosamente el mute de Teams.

## Contexto auditado (verificar de nuevo en el código, no asumir)

- `camvoice/audio/capture.py`, `vad.py`, `gate.py` y `pipeline.py`: micrófono físico, VAD y `SpeechGate`.
- `camvoice/video/engine.py`: lectura de `VoiceStatus` y envío de `VoiceInput` al director.
- `camvoice/video/director.py`: estados de habla/silencio, transiciones, gestos, pausa.
- `camvoice/ui/app.py`, `gestures_tab.py`, `hotkeys.py`, `tray.py`: interfaz, atajos y bandeja.
- `camvoice/config.py`: configuración persistente.
- El mute de Teams es **a nivel de aplicación**: el micrófono físico puede seguir capturando aunque Teams esté silenciado. **No** usar nivel RMS, `SpeechGate` o el mute global de Windows como sustitutos del estado de Teams.

Comienza leyendo `git status`, el código efectivo y el documento `docs/ANALISIS_FORENSE_LOGICA_CAMVOICE.md`. Respeta todos los cambios locales sin commit. Nunca sobrescribas ni reviertas trabajo previo.

## A. Detección automática de Teams — lectura, no control

1. Diseña una interfaz desacoplada, por ejemplo `MeetingMuteProvider` con estados explícitos:
   - `NO_MEETING`
   - `MUTED`
   - `UNMUTED`
   - `UNKNOWN` (error, carga, varias reuniones, texto ambiguo, sesión desconectada o datos caducados)

2. Para **Teams de escritorio de Windows**, investiga y utiliza *Windows UI Automation* en **modo lectura**. Busca la ventana de reunión correcta del proceso Teams y su control de micrófono. En versiones conocidas se ha observado `AutomationId = microphone-button`; **verifícalo en la versión instalada**, no lo des por garantizado. La etiqueta suele describir la **acción disponible**, no el estado: «Mute mic» suele significar *actualmente activado*; «Unmute mic», *actualmente silenciado*. Admite interfaces en español/inglés mediante propiedades de accesibilidad verificadas. No uses comparación por píxeles, reconocimiento visual frágil, búsqueda por título únicamente ni shortcuts que alteren Teams.

3. **No utilices** la antigua API local de terceros de Teams (WebSocket `localhost:8124`): fue retirada el **30-06-2026**. No prometas que Microsoft Graph devuelve el estado local de mute de esta ventana. La UI Automation es **no oficial y susceptible de cambios**: si falla, publicar `UNKNOWN`.

4. Ejecuta la observación de Teams **fuera del hilo de render** y fuera de los callbacks de audio. Respeta el modelo de hilos/COM que exija UI Automation, usa tiempo de espera, reexplora elementos tras cambios de WebView2 y evita referencias obsoletas. No robar foco, no pulsar botones, no cambiar estado de Teams, no requerir administrador ni una sesión en la nube. Limita consultas y consumo de CPU.

5. Estado publicado con `timestamp` y origen. Al perder Teams, su ventana, la reunión, el botón o la confianza de lectura, cambia inmediatamente a `UNKNOWN`/`NO_MEETING` según corresponda. **Nunca conservar `UNMUTED` indefinidamente**; aplicar expiración corta y configurable (p. ej. 300–500 ms; justificar el valor real). Al confirmar `MUTED`, vetar la salida de habla desde el siguiente ciclo del motor, sin saltos innecesarios de imagen.

6. En caso de más de una ventana/cuenta/instancia, no adivinar cuál controla la cámara. Si no puede identificarse inequívocamente la reunión activa, devolver `UNKNOWN` y mostrarlo.

## B. Autorización manual — visible y reversible

1. Añade en la UI un selector claramente rotulado **«Control de habla»**:
   - `Automático (Teams)` — predeterminado para sesiones de Teams. Permitir habla solo cuando `meeting_state == UNMUTED` **y** hay voz local.
   - `Manual` — interruptor **«Autorizar habla» / «Bloquear habla»**, con valor inicial **Bloquear** en cada arranque y cuando se pierde la sesión.

2. Incorpora un atajo global configurable para alternar el permiso manual, evitando colisiones con los atajos de gestos existentes; indica de forma inequívoca el estado en la ventana y bandeja. El control manual **no abre el micrófono de Teams**: solo habilita o veta la selección de vídeo de habla de CamVoice.

3. **Regla de seguridad entre ambos mecanismos:** una detección confiable de `Teams = MUTED` o `NO_MEETING` **siempre prevalece** sobre el permiso manual cuando el perfil Teams está seleccionado. Si la integración está en `UNKNOWN`, el modo Automático permanece bloqueado. Un modo Manual independiente puede habilitarse explícitamente como respaldo, pero debe mostrar advertencia persistente: **no puede verificar el mute real de Teams** y no debe afirmar que está sincronizado. Nunca cambiar automáticamente a Manual para eludir `UNKNOWN`.

4. No enviar a `SPEAKING` al cambiar de modo, habilitar permisos o recuperar Teams, salvo que **también** el VAD/`SpeechGate` confirme voz local actual. Al bloquear, pasar al destino visual de silencio usando el mecanismo de transición existente sin modificar su algoritmo. Si ya hay un gesto, conservar sus reglas de cancelación actuales, salvo el mínimo indispensable para evitar que un vídeo de habla siga simulando conversación cuando la autorización está bloqueada.

5. Dejar el modo manual fácil de entender para Teams y Meet, pero **no afirmar integración automática con Meet**. Si decides incluir un perfil genérico, debe usar permiso manual y mostrar claramente que no detecta el mute de la reunión. No ampliar el alcance a una integración de navegador no solicitada.

## Único punto de autorización en el pipeline

Centraliza la decisión **después de VAD + `SpeechGate` y antes de entregar el estado al `Director`**, para no duplicar reglas ni alterar la detección de audio existente:

```python
voice_authorized = permission_controller.effective_allowed()
visual_speaking = voice_authorized and speech_gate_state == SPEAKING
```

Preserva las estructuras que de verdad existan; el pseudocódigo **no exige** esos nombres. Si no autorizado, el director debe recibir una señal coherente de silencio (también neutralizar banderas de micro-pausa/otros indicadores contradictorios de habla), sin perder las métricas internas del detector. Distingue en la UI «voz física detectada» de «habla autorizada» para diagnosticar errores. No suspender ni manipular el micrófono físico.

**Matriz de comportamiento obligatoria:**

| Teams / perfil | Permiso manual | Voz física | Vídeo esperado |
|---|---|---|---|
| Teams en reunión, MUTED | cualquiera | sí | SILENT |
| Teams en reunión, MUTED | cualquiera | no | SILENT |
| Teams en reunión, UNMUTED, automático | no aplica | sí | SPEAKING |
| Teams en reunión, UNMUTED, automático | no aplica | no | SILENT |
| Teams UNKNOWN, automático | no aplica | sí/no | SILENT |
| Sin reunión Teams, automático | no aplica | sí/no | SILENT |
| Manual explícito, sin estado Teams confiable | Bloquear | sí/no | SILENT |
| Manual explícito, sin estado Teams confiable | Autorizar | sí | SPEAKING, con aviso de que no se verificó Teams |
| Manual explícito, sin estado Teams confiable | Autorizar | no | SILENT |

En un perfil Teams, `MUTED` confirmado nunca se puede anular desde la UI de CamVoice. Aclara los límites de la rama Manual en la interfaz.

## Restricciones de alcance

- **No modificar:** clips, manifest de las 62 escenas, selección de biblioteca, algoritmos de fotogramas ancla, fundidos, gestos, vídeo normalizado ni calibración VAD/AEC preexistente.
- Cambiar solo los módulos imprescindibles para proveedor de estado de Teams, autorización efectiva, configuración y controles UI/atajos.
- No introducir servicios externos, cuentas, recopilación ni persistencia de audio. No registrar conversaciones, voces ni contenido de reuniones. Logs de estados y fallos técnicos sin datos personales.
- Mantener procesamiento local, rendimiento de render, compatibilidad del proyecto y ajustes anteriores. No ocultar de manera engañosa si una imagen es pregrabada.
- **No ejecutar** `pytest`, `soak`, benchmarks, pruebas de cámara ni reuniones simuladas: el usuario realizará pruebas funcionales. Permito revisión estática de código y sintaxis sin escribir artefactos innecesarios.
- No anunciar cobertura completa si la versión instalada de Teams no expone un estado accesible. En ese caso dejar el modo automático en `UNKNOWN`/bloqueado y el modo manual claramente separado; informar la limitación.

## Entrega

Implementa los cambios **ahora** en los archivos mínimos necesarios. Al finalizar muestra **solamente**:
1. Archivos modificados y qué cambió en cada uno.
2. Resultado de la revisión estática (sin atribuir pruebas no ejecutadas).
3. Estado de la integración con el Teams realmente instalado: `FUNCIONAL VERIFICABLE`, `DEPENDIENTE DE VALIDACIÓN MANUAL` o `NO DISPONIBLE`, con evidencia breve.
4. Pasos de prueba **manual** (Teams silenciado + conversación local; Teams activo + voz; Teams activo + silencio; Teams no abierto; pérdida de lectura; modo Manual bloquear/autorizar).
5. Limitaciones y confirmación explícita: **«Implementación finalizada; pruebas reales pendientes del usuario»**.

No prometas que una señal de micrófono permite distinguir interlocutores ni que un proveedor UIA es infalible. Prioriza seguridad de estado y comportamiento predecible por encima de comodidad.
