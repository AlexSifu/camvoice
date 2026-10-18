# Requisitos para probarlo en una máquina nueva

Respuesta corta: **copiar una carpeta, ejecutar un comando y reiniciar la
aplicación de videollamada.** Nada más.

---

## 1. Lo que NO hace falta

Conviene decirlo primero, porque es lo que normalmente se da por supuesto:

| | |
|---|---|
| ❌ **OBS Studio** | La aplicación trae su propio filtro de cámara |
| ❌ **Python** | Va embebido en el ejecutable |
| ❌ **Visual C++ Redistributable** | Las DLL del runtime van dentro del paquete |
| ❌ **.NET / Java / cualquier runtime** | No se usa ninguno |
| ❌ **Drivers** | El filtro es un COM de modo usuario, no un driver de kernel: no hay firma de controlador ni arranque en modo de prueba |
| ❌ **Conexión a Internet** | Ni para instalar ni para funcionar |
| ❌ **Tarjeta gráfica dedicada / CUDA** | Gráficos integrados sobran |
| ❌ **Cuenta, licencia o clave de API** | No hay nada que activar |
| ❌ **Instalador** | Se descomprime y se usa |

Verificado: se analizaron las dependencias de los 335 archivos del paquete y
**todas las DLL externas que quedan son componentes del propio Windows**
(`kernel32`, `user32`, `ole32`, `d2d1`, `DWrite`, `ncrypt`, …).

---

## 2. Lo que SÍ hace falta

### 2.1. La máquina

| Requisito | Mínimo | Comentario |
|---|---|---|
| Sistema | **Windows 10 o 11, 64 bits** | No funciona en 32 bits ni en Windows 7/8 |
| Procesador | Intel Core i5 o equivalente | Medido: 9 % de un núcleo |
| RAM | 8 GB | La aplicación usa unos 170 MB |
| Disco | **~1,5 GB libres** | 230 MB el programa + unos 7 MB por segundo de clip en caché |
| Micrófono | Cualquiera | Integrado o USB |
| Permisos | **Administrador una sola vez** | Solo para registrar la cámara |

> **Lo del disco sorprende y conviene tenerlo en cuenta.** Los clips se
> pre-decodifican a fotogramas en crudo para no decodificar video en el bucle de
> salida. Dos clips de 15 s a 640×480 ocupan unos **210 MB** en
> `%APPDATA%\camvoice\clipcache`. Es el precio del rendimiento; se vacía con
> `camvoice-cli clean`.

### 2.2. Tus materiales

| | |
|---|---|
| **Clip hablando** | 8-20 s, hablando con normalidad |
| **Clip en silencio** | 8-20 s, callado, boca cerrada |

Grábalos **seguidos, sin mover la cámara ni cambiar la luz**, y que el **primer
fotograma de los dos** tenga la boca cerrada y la mirada al frente: es la pose
desde la que se reanuda el habla tras un silencio largo.

MP4/H.264 o AVI/MJPEG son apuestas seguras. `camvoice-cli probe video.mp4` dice
si un archivo sirve.

**Para probar sin grabar nada:** `camvoice-cli make-clips --outdir clips` genera
dos clips sintéticos que ejercitan la cadena completa.

---

## 3. Pasos en la máquina nueva

### Automático (recomendado)

```
1.  Copia la carpeta camvoice\ (230 MB) donde quieras.

2.  Entra en  instalacion\  y haz doble clic en:

        INSTALAR.bat        -> solo para tu usuario, sin administrador
        PROBAR.bat          -> abre el navegador y verás la imagen

3.  Si algo falla:   DIAGNOSTICO.bat
    Para quitarlo:   DESINSTALAR.bat
```

Los scripts comprueban la máquina, registran la cámara en 64 y 32 bits,
verifican el resultado leyendo el registro de Windows y lanzan una prueba
completa con clips sintéticos. Ver `instalacion\00-LEEME.md`.

### A mano

```
1.  Copia la carpeta camvoice\ donde quieras.

2.  camvoice-cli install-camera      (sin administrador; una sola vez por usuario)
3.  camvoice-cli verify-camera       (debe terminar con "RESULTADO: correcto…")
    camvoice-cli doctor              (debe terminar con "Todo listo.")
4.  Doble clic en camvoice.exe
5.  Selecciona tus dos clips y tu micrófono, y pulsa Iniciar.
6.  Abre Teams / Zoom / Meet DESPUÉS y elige «CamVoice Virtual Camera».
```

El paso 6 importa: las aplicaciones de videollamada **enumeran las cámaras al
arrancar**. Si ya estaban abiertas, ciérralas y vuelve a abrirlas.

---

## 4. Comprobación rápida de que funciona

Sin necesidad de Teams ni de tus clips:

```
camvoice-cli doctor                           # entorno, audio y cámara
camvoice-cli cameras                          # debe listar CamVoice en 64 y 32 bits
camvoice-cli make-clips --outdir clips        # clips de prueba
camvoice-cli run --speaking clips\habla-prueba.mp4 ^
                 --silent  clips\silencio-prueba.mp4 --duration 20
camvoice-cli vad-test --duration 30           # ajusta la sensibilidad del micrófono
```

Para ver la imagen de verdad, abre <https://webcamtests.com/> en Chrome o Edge y
elige **CamVoice Virtual Camera**. Es la prueba más rápida y no requiere cuenta.

**No uses la app Cámara de Windows.** No soporta DirectShow y no verá la cámara;
eso no es un fallo.

---

## 5. Dónde están los límites

| Límite | Detalle |
|---|---|
| **Aplicaciones que la ven** | Teams, Zoom, Meet, Discord, Slack, OBS, Chrome, Edge y todo lo basado en Chromium. **No** la app Cámara de Windows (solo Media Foundation) |
| **Un productor a la vez** | Dos copias de camvoice no pueden alimentar el mismo filtro |
| **Administrador** | Solo para el paso 2. El uso diario no lo necesita |
| **Windows en ARM** | No probado |

---

## 6. Si algo falla

| Síntoma | Qué pasa |
|---|---|
| «No hay ninguna cámara virtual registrada» | Falta el paso 2 |
| No aparece en Teams/Zoom/Chrome | Cierra y vuelve a abrir esa aplicación |
| No aparece en la app Cámara de Windows | Normal, ver §5 |
| «La cámara virtual ya está en uso» | Otra copia de camvoice, u OBS con su cámara iniciada |
| «El micrófono no entrega señal audible» | Silenciado en Windows, o volumen de entrada a cero |
| El detector se activa solo | `camvoice-cli vad-test --duration 30` y ajusta según `INSTALACION.md` §4.3 |
| `camvoice.exe` arranca y se cierra | Lánzalo desde consola con `camvoice-cli doctor` para ver el error |

---

## 7. Para dejar la máquina como estaba

```
camvoice-cli install-camera --uninstall     # quita el registro de la cámara
rmdir /s /q "%APPDATA%\camvoice"            # configuración y caché
```

Y borra la carpeta. **Haz el primer comando antes de borrar la carpeta**: si no,
el registro queda huérfano y haría falta volver a descomprimir para limpiarlo.

---

## 8. Nota sobre el antivirus

El ejecutable **no está firmado digitalmente**. En una máquina nueva, Windows
SmartScreen puede mostrar «Windows protegió su PC» la primera vez: *Más
información → Ejecutar de todas formas*. Algunos antivirus corporativos también
marcan ejecutables de PyInstaller sin firmar.

Si esto va a desplegarse en equipos de una organización, hace falta un
certificado de firma de código. No afecta al funcionamiento, solo a los avisos.
