# Dependencias y licencias

Soporte documental de **CA-02** (ausencia de LLMs) y **CA-14**.

## 1. Dependencias de ejecución

Son siete. Todas tienen rueda precompilada para Windows x64, así que la
instalación no necesita compilador.

| Paquete | Versión | Licencia | Para qué |
|---|---|---|---|
| [NumPy](https://numpy.org/) | ≥ 1.26, < 3 | BSD-3-Clause | Operaciones sobre los búferes de audio y de fotogramas |
| [opencv-python-headless](https://github.com/opencv/opencv-python) | 4.10.0.84 | Apache-2.0 (OpenCV); MIT (el empaquetado) | Decodificar los clips, escalar, convertir a NV12, mezclar el fundido |
| [sounddevice](https://python-sounddevice.readthedocs.io/) | 0.5.1 | MIT (y PortAudio, MIT) | Capturar el micrófono |
| [webrtcvad-wheels](https://github.com/daanzu/py-webrtcvad-wheels) | 2.0.14 | MIT (el envoltorio); BSD-3-Clause (el código de WebRTC) | Detección de actividad de voz |
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) | 0.15.0 | GPL-2.0 | Escribir en la cámara virtual |
| [PySide6-Essentials](https://doc.qt.io/qtforpython/) | 6.8.1.1 | LGPL-3.0 (y GPL-2.0/GPL-3.0 como alternativas) | Interfaz gráfica |
| [psutil](https://github.com/giampaolo/psutil) | ≥ 5.9 | BSD-3-Clause | Medir CPU y memoria del proceso |

Dependencias transitivas: `cffi` y `pycparser` (ambas MIT), que arrastra
`sounddevice`, y `shiboken6` (LGPL-3.0), que arrastra PySide6.

### 1.1. Dos elecciones que no son las obvias

**`webrtcvad-wheels` y no `webrtcvad`.** Es el mismo código fuente. El paquete
`webrtcvad` original no publica rueda para Windows: `pip install webrtcvad`
intenta compilar y falla si no están instaladas las Visual C++ Build Tools, que
son varios gigabytes. `webrtcvad-wheels` publica ruedas precompiladas.

Efecto secundario a tener en cuenta al empaquetar: el **módulo** se llama
`webrtcvad` pero los **metadatos** de la distribución están bajo
`webrtcvad-wheels`, y el hook que trae PyInstaller falla por eso. El proyecto
incluye `build/hooks/hook-webrtcvad.py` para corregirlo.

**`opencv-python-headless` y no `opencv-python`.** La variante normal incluye sus
propios plugins de plataforma de Qt, que entran en conflicto con los de PySide6.
El síntoma es un fallo al crear la ventana, con un mensaje sobre el plugin
`windows` de Qt que no apunta a la causa. La variante `headless` no trae nada de
Qt y aquí no se pierde nada: toda la interfaz es PySide6 y OpenCV se usa solo
para cálculo.

## 2. Dependencias de desarrollo

No se distribuyen con la aplicación.

| Paquete | Licencia | Para qué |
|---|---|---|
| pytest | MIT | Pruebas |
| pytest-timeout | MIT | Cortar pruebas colgadas |
| PyInstaller | GPL-2.0 con excepción de enlace | Generar el ejecutable |

La excepción de enlace de PyInstaller permite distribuir el ejecutable
resultante bajo la licencia que uno quiera.

## 3. Software externo requerido

| Software | Licencia | Papel |
|---|---|---|
| [OBS Studio](https://obsproject.com/) | GPL-2.0 | Su instalador registra el filtro DirectShow de la cámara virtual |

**No se redistribuye nada de OBS.** La aplicación no empaqueta ni copia
`obs-virtualcam-module64.dll`: habla con el filtro que el instalador de OBS
registró en el sistema. OBS Studio no necesita estar abierto.

## 4. Implicaciones de licencia para distribuir

`pyvirtualcam` es **GPL-2.0**. Una aplicación que lo enlaza y se distribuye como
un todo queda sujeta a la GPL-2.0: si distribuyes el ejecutable a terceros,
tienes que ofrecer el código fuente completo bajo términos compatibles.

Si eso es un problema para el uso previsto, las dos salidas razonables son:

1. **Sustituir la salida de video.** `output/sink.py` aísla toda la dependencia
   de `pyvirtualcam` detrás del protocolo `FrameSink`. Escribir un sumidero
   alternativo (por ejemplo contra el controlador de UnityCapture, o uno propio)
   no toca ninguna otra parte del código.
2. **Distribuir el código fuente** y dejar que cada usuario instale las
   dependencias con `pip install -r requirements.txt`.

PySide6 bajo **LGPL-3.0** permite distribuir una aplicación propietaria siempre
que Qt se enlace dinámicamente —que es lo que hace PyInstaller, con las DLL
sueltas— y que se permita al usuario sustituir esas bibliotecas.

## 5. Por qué ninguna dependencia es un LLM ni IA generativa (CA-02)

- **Ninguna requiere clave de API, cuenta, suscripción ni servicio remoto.**
  Ninguna abre conexiones de red: `sounddevice` y `pyvirtualcam` hablan con el
  sistema local, OpenCV lee archivos, PySide6 dibuja ventanas, psutil lee
  contadores del sistema operativo.
- **No hay pesos de modelo en ningún paquete.** El tamaño total instalado son
  unos 250 MB, de los cuales la gran mayoría es Qt.
- **WebRTC VAD no es un modelo de machine learning entrenable.** Es un
  clasificador de mezclas gaussianas sobre seis sub-bandas de frecuencia, con
  coeficientes fijos escritos en el código fuente de WebRTC en 2011. No se
  entrena, no se ajusta, no infiere nada sobre el contenido: devuelve un
  booleano por trama de 20 ms.
- **No hay generación de imagen de ningún tipo.** Los fotogramas que salen son
  recortes y mezclas lineales de los fotogramas que el usuario grabó. El único
  píxel que la aplicación crea de cero es el de las barras del modo «caber
  entero» y el de los clips sintéticos de prueba, que son elipses dibujadas con
  OpenCV.
- **No se envía nada a ninguna parte.** El audio se analiza trama a trama y se
  descarta; no se graba, no se almacena y no se transmite.

Comprobable con `camvoice-cli doctor`, que enumera lo que está cargado, y
desactivando el adaptador de red: todo sigue funcionando.
