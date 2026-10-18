# Procedimiento de pruebas

Este documento es la lista de comprobación de los criterios de aceptación
(CA-01 a CA-14 de `plan.md` §6). Separa lo que se verifica automáticamente de lo
que exige una persona delante.

**Regla:** ningún criterio se marca cumplido sin haberlo ejecutado. El estado
actual está en `plan.md` §8 y los resultados de rendimiento en `METRICS.md`.

---

## 1. Pruebas automatizadas

```
pytest                    # 341 pruebas (+5 de integración real, opcionales), ~18 s
pytest -v                 # con el nombre de cada caso
pytest tests/test_gate.py # solo la histéresis
```

No necesitan micrófono, ni cámara virtual, ni videos: los clips sintéticos se
generan al vuelo y la configuración se aísla en un directorio temporal mediante
`CAMVOICE_HOME`.

Si falta `webrtcvad`, las pruebas de ese backend se omiten (`skip`) en lugar de
fallar; el resto sigue cubriendo el detector de energía.

| Archivo | Qué cubre | Criterios |
|---|---|---|
| `test_gate.py` | Latencia de activación y cierre exactas, tolerancia a pausas, permanencias mínimas, resistencia al ruido | CA-04, CA-05 |
| `test_vad.py` | Remuestreo (continuidad, antialiasing, amplitud), detector de energía, piso de ruido, entrada muda, WebRTC | CA-04, CA-05, CA-12 |
| `test_composer.py` | Bucle sin costura, transiciones monótonas, inversión a mitad, rebobinado, ausencia de negros, 30 min simulados | CA-06, CA-08 |
| `test_cache.py` | Formato NV12, equivalencia del fundido en YUV y RGB, geometría, clave de caché, archivos corruptos | CA-03, CA-10 |
| `test_engine.py` | Reloj sin deriva, cadencia real del motor, fallo de la salida, validación de configuración, métricas | CA-06, CA-08, CA-09, CA-12 |
| `test_filters.py` | Registro por usuario en un registro simulado: solo `HKCU`, nunca `HKLM`, claves y vistas exactas, hashes, idempotencia, reversión ante fallos, conflictos con el sistema, DLL en uso, rutas con acentos; ausencia de `regsvr32`/`runas` en código y scripts | CA-07, CA-13 |
| `test_camara_cli.py` | Códigos de salida de `install-camera` (0/1/2) y `verify-camera` | CA-07, CA-13 |
| `test_camara_por_usuario_integracion.py` | **Opcional, real**: instala en `HKCU`, enumera en 64 y 32 bits, envía y lee fotogramas, desinstala. Solo con `CAMVOICE_TEST_REAL_CAMERA=1` y sin elevar | CA-07 |
| `test_cli.py` | Punto de entrada, subcomandos, recorrido completo como proceso | CA-03, CA-13 |
| `test_effects.py` | Cada efecto, independencia de la resolución, rangos, coste | CA-09, CA-10 |
| `test_ui.py` | Ventana, pestañas, los controles escriben en la configuración, bloqueo en caliente | CA-10, CA-11 |
| `test_record.py` | Guion sin huecos, mezcla de sonidos del texto, cadencia escrita, detección de cámaras virtuales | CA-03, CA-11 |

### Las dos pruebas que conviene conocer

**`test_el_ruido_de_fondo_no_abre_la_puerta`** (CA-05). Alimenta la puerta con
30 s de un positivo de VAD cada 100 ms —el patrón que produce el ruido
ambiente— y exige **cero** activaciones. Esta prueba detectó un fallo real:
histéresis y tolerancia a pausas implementadas literalmente se contradicen, y el
ruido abría la puerta por acumulación lenta. De ahí salió la guarda de ciclo de
trabajo.

**`test_mezclar_en_nv12_equivale_a_mezclar_en_bgr`**. Verifica numéricamente la
premisa que hace válido todo el diseño de la caché: que el fundido cruzado se
puede calcular sobre los bytes NV12 crudos. Si esa equivalencia no se cumpliera,
las transiciones tendrían artefactos de color.

---

## 2. Comprobación del entorno (CA-01, CA-02, CA-07)

```
camvoice-cli doctor
```

Comprueba versiones de dependencias, disponibilidad de WebRTC VAD, dispositivos
de audio, apertura real de la cámara virtual y validez de la configuración.
Devuelve código de salida distinto de cero si algo impide funcionar.

**CA-01, ejecución sin red.** Desactiva el adaptador de red y repite `doctor`,
`prepare` y `run`. Todo debe funcionar igual. Ninguna dependencia abre sockets:
`sounddevice` y `pyvirtualcam` hablan con el sistema local, OpenCV lee archivos
y PySide6 dibuja ventanas.

**CA-02, ausencia de LLMs.** `LICENCIAS.md` enumera las siete dependencias de
ejecución con su licencia y su función. Ninguna requiere clave, cuenta ni
servicio. WebRTC VAD es un clasificador de mezclas gaussianas con coeficientes
fijos: no es un modelo entrenable ni generativo.

---

## 3. Detector de voz con micrófono real (CA-04, CA-05)

```
camvoice-cli vad-test --duration 30
```

Imprime, cada 250 ms: estado de la puerta, decisión de la trama, nivel en dBFS,
piso de ruido, relación señal/ruido y un medidor. Al final, la cronología de
cambios con marcas de tiempo y la tasa de cambios por minuto.

### Guion de la prueba

| Paso | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | 10 s de silencio | 0 cambios de estado. El piso de ruido se estabiliza |
| 2 | Decir «uno, dos, tres» | SPEAKING en 100-200 ms desde el inicio de la voz |
| 3 | Callar | SILENT unos 450 ms después (`release_ms`) |
| 4 | Contar hasta diez con pausas normales | **Un solo** tramo SPEAKING, sin cortes entre números |
| 5 | Teclear, mover papeles, poner un ventilador | 0 cambios de estado |
| 6 | Poner música de fondo baja y hablar encima | SPEAKING al hablar; sin alternancias con la música sola |

Los pasos 2 y 3 verifican CA-04. Los pasos 5 y 6, CA-05. Si el paso 5 falla,
ajusta según la tabla de `INSTALACION.md` §4.3 y repite: **la sensibilidad
depende del micrófono y de la sala, y no hay un valor por defecto que sirva para
todos.**

---

## 4. Reproducción y transiciones (CA-06)

```
camvoice-cli run --speaking hablando.mp4 --silent quieto.mp4 --no-virtual-cam
```

O desde la interfaz, mirando la vista previa. Con la vista previa delante, habla
y calla varias veces durante dos minutos y comprueba:

- [ ] No aparece **ningún fotograma negro** en los cambios.
- [ ] El fundido se percibe suave, no como un corte.
- [ ] Al volver al silencio, la boca queda cerrada.
- [ ] Tras un silencio largo, el habla **no** reanuda con la boca a medio abrir.
- [ ] El bucle de cada clip **no se nota**: no hay un salto periódico. Si se
      nota, comprueba que el modo de bucle es «ida y vuelta».
- [ ] Hablar muy brevemente (una sílaba) no produce un parpadeo de ida y vuelta.
- [ ] El recorte del encuadre es el esperado. Si molesta, prueba el modo «caber
      entero».

---

## 4.5. Grabador guiado

Requiere una webcam.

1. Pestaña **Grabar** → **Buscar**. Debe listar tus cámaras físicas y **no**
   «CamVoice Virtual Camera».
2. Elige clip HABLANDO, 15 s, y pulsa **Grabar**.
3. Comprueba:
   - [ ] Aparecen las guías de encuadre (eje central, línea de ojos, marco).
   - [ ] Cuenta atrás de 3 s con la señal «BOCA CERRADA» destacada.
   - [ ] El texto sale **una frase cada vez**, no el párrafo entero.
   - [ ] Debajo se ve atenuada la frase siguiente.
   - [ ] **Nada se mueve de sitio** al cambiar de frase: los botones y la
         barra de progreso se quedan donde estaban.
   - [ ] La cuenta atrás respeta el valor del deslizador.
   - [ ] **Para sola** a los 15 s.
   - [ ] El archivo resultante **dura 15 s al reproducirlo**, no menos. Si dura
         menos, la cadencia escrita no coincide con la real.
   - [ ] Ofrece usarlo como clip y al aceptar queda seleccionado.
4. Repite con el clip EN SILENCIO y comprueba que el guion es distinto.

Si la cámara entrega menos de 20 FPS reales, el aviso lo indica: suele ser falta
de luz, no un fallo.

## 4.6. Bandeja del sistema

1. En **Salida**, marca «seguir en el área de notificación».
2. Inicia la sesión y **cierra la ventana** con la X.
3. Comprueba:
   - [ ] La ventana desaparece de la barra de tareas y de Alt+Tab.
   - [ ] Aparece un globo avisando de que sigue funcionando.
   - [ ] El icono junto al reloj **cambia de color** al hablar.
   - [ ] La cámara virtual **sigue emitiendo** (compruébalo en el navegador).
   - [ ] Doble clic en el icono devuelve la ventana.
   - [ ] «Salir» en el menú del icono termina el proceso de verdad.
4. Desmarca la casilla y comprueba que entonces la X cierra la aplicación.

## 5. Cámara virtual (CA-07)

Esta prueba **requiere una persona**: hay que mirar la imagen en una aplicación
receptora real.

1. `camvoice-cli install-camera` si todavía no lo hiciste (una sola vez por
   usuario; desde una consola **sin elevar**, no pide administrador).
2. `camvoice-cli verify-camera` debe terminar con `RESULTADO: correcto…`, y
   `camvoice-cli cameras` listar **CamVoice Virtual Camera** en 64 y 32 bits
   como «este usuario».
3. `camvoice-cli doctor` debe reportar la cámara disponible.
4. Arranca `camvoice.exe` y pulsa Iniciar, con «Enviar a la cámara virtual»
   marcado.
5. Abre una de estas y selecciona **CamVoice Virtual Camera**:

   | Aplicación | Dónde |
   |---|---|
   | Chrome / Edge | `https://webcamtests.com/` o cualquier videollamada web |
   | Microsoft Teams | Configuración > Dispositivos > Cámara |
   | Zoom | Configuración > Video > Cámara |
   | Google Meet | Configuración > Video > Cámara |

6. Comprueba:
   - [ ] La imagen se ve, con la orientación y el encuadre correctos.
   - [ ] Al hablar, el video cambia al clip de habla con el retardo esperado.
   - [ ] No hay parpadeos ni fotogramas negros.
   - [ ] La relación de aspecto es correcta: la cara no está deformada.

**No uses la app Cámara de Windows:** no soporta DirectShow y no verá la cámara.
No es un fallo.

Si no aparece en la lista de la aplicación receptora, ciérrala y vuelve a
abrirla: enumera las cámaras al arrancar.

---

## 6. Continuidad de 30 minutos (CA-08)

```
camvoice-cli bench --duration 1800 --metrics
```

Déjalo correr 30 minutos. Habla de vez en cuando para incluir transiciones. Al
final emite un veredicto automático sobre:

- **FPS sostenidos** ≥ 97 % del objetivo.
- **CPU** por debajo del 25 % de un núcleo.
- **Memoria estable**: pendiente por mínimos cuadrados < 1 MB/min.

Comprobaciones manuales durante la prueba:

- [ ] La vista previa (o la aplicación receptora) sigue mostrando imagen a los
      30 minutos.
- [ ] No hay degradación visible de la cadencia.
- [ ] El Administrador de tareas no muestra crecimiento sostenido de memoria.
- [ ] `late_frames` se mantiene bajo y `resyncs` en 0 o cerca.

El CSV queda en `%APPDATA%\camvoice\metrics\` con una fila por muestra, para
graficar la evolución si hiciera falta.

---

## 7. Rendimiento (CA-09)

Debe medirse **en el equipo objetivo**: un Core i5 o equivalente con gráficos
integrados. Las cifras de un equipo no se trasladan a otro.

```
camvoice-cli bench --duration 300 --metrics                   # con cámara virtual
camvoice-cli bench --duration 300 --metrics --no-virtual-cam  # coste del motor solo
```

La segunda medición aísla el coste de la cámara virtual restándola de la
primera. Anota los dos resultados en `METRICS.md` siguiendo la plantilla que hay
allí; el informe ya incluye la identificación del equipo.

---

## 8. Interfaz (CA-11)

Con la sesión **en marcha**, sin detenerla ni reiniciar la aplicación:

- [ ] Cambiar de micrófono en el desplegable. Debe recalibrar y seguir
      funcionando (la barra de estado lo indica).
- [ ] Mover los deslizadores de sensibilidad y tiempos. El efecto debe ser
      inmediato y visible en el indicador de estado.
- [ ] Desmarcar y volver a marcar «Enviar a la cámara virtual».
- [ ] Mover la transición a 0 ms y a 600 ms y notar la diferencia.
- [ ] Comprobar que los controles de clips, resolución, FPS, encuadre y espejo
      están **deshabilitados** (invalidan la caché; requieren reiniciar).
- [ ] En la pestaña **Efectos**, mover desenfoque y pixelado: el cambio debe
      verse en la vista previa **al instante**, sin volver a preparar.
- [ ] Aplicar un preajuste y comprobar que reemplaza los valores anteriores en
      lugar de sumarse a ellos.
- [ ] Subir el desenfoque al 100 % y verificar con el Administrador de tareas
      que la CPU sigue siendo baja.
- [ ] Detener y volver a iniciar sin cerrar la aplicación.
- [ ] Cerrar la ventana: el proceso debe terminar, sin hilos colgados.

---

## 9. Recuperación de errores (CA-12)

Automatizado: el fallo de la cámara virtual en marcha
(`test_un_fallo_de_la_salida_no_detiene_el_motor`) y la entrada muda
(`test_una_entrada_muda_se_detecta_y_se_avisa`).

Manual, con la sesión en marcha:

| Prueba | Qué hacer | Qué debe pasar |
|---|---|---|
| Micrófono desconectado | Desenchufar el micrófono USB | Mensaje legible, estado forzado a SILENT, video sigue saliendo. Al volver a enchufarlo, se recupera solo |
| Micrófono silenciado | Silenciarlo en Windows | Aviso explícito tras unos 5 s |
| Cámara virtual tomada | Abrir OBS y pulsar «Iniciar cámara virtual» | Mensaje que nombra la causa; el motor sigue y la vista previa funciona |
| Clip borrado | Borrar un `.raw` de `clipcache` con la sesión en marcha | Imagen congelada en el último fotograma bueno, con aviso. **Nunca negro** |
| Configuración corrupta | Escribir basura en `config.json` y arrancar | Arranca con valores por defecto; el archivo malo queda como `config.json.bad` |
| Video inexistente | Mover un clip y pulsar Iniciar | Mensaje indicando qué archivo falta |

En ningún caso debe cerrarse la aplicación ni quedarse sin responder.

---

## 10. Distribución (CA-13)

```
pyinstaller build/camvoice.spec --noconfirm
```

Comprobaciones sobre `dist/camvoice/`:

- [ ] Existen **ambos** ejecutables: `camvoice.exe` y `camvoice-cli.exe`.
- [ ] `_internal` contiene PySide6, cv2, numpy, sounddevice, pyvirtualcam y
      psutil. **Esto hay que mirarlo:** las dependencias se importan de forma
      diferida y una primera versión del `.spec` produjo un ejecutable de 20 MB
      sin ninguna de ellas, que compilaba sin errores y fallaba al arrancar.
- [ ] `_internal/filters/` contiene `UnityCaptureFilter64.dll` y
      `UnityCaptureFilter32.dll`.
- [ ] `camvoice-cli.exe doctor` funciona y reporta todo correcto.
- [ ] `camvoice-cli.exe install-camera` registra la cámara sin UAC, y
      `verify-camera` termina con `RESULTADO: correcto`.
- [ ] `camvoice.exe` abre la ventana con doble clic, sin consola.
- [ ] **En una máquina limpia** (sin Python y sin el entorno virtual): se
      descomprime, se instala OBS Studio y funciona.
- [ ] Las instrucciones de desinstalación de `INSTALACION.md` §7 dejan el
      sistema limpio.
