# Validación: cámara virtual instalada solo para el usuario actual

Estado: **PARCIAL**. Lo que se ha probado funciona sin administrador y sin tocar
`HKLM`; faltan pruebas que requieren otras máquinas, otra cuenta o aplicaciones
de videollamada (§7). Nada de lo marcado «NO EJECUTADA» debe darse por bueno.

Fecha de las pruebas: 2026-10-18.

---

## 1. Entorno

| | |
|---|---|
| Sistema | Windows 11 Pro, build 26300, 64 bits |
| Cuenta | `EQUIPO\usuario`, miembro de Administradores **pero en sesión sin elevar** (`IsInRole(Administrator)` = `False`, token filtrado por UAC) |
| Python | 3.12.10 en `.venv`, pyvirtualcam 0.15.0, OpenCV 4.10.0, PyInstaller 6.22.3 |
| Filtro | UnityCapture: `UnityCaptureFilter64.dll` (SHA-256 `72812f53…de25e5`) y `UnityCaptureFilter32.dll` (`aa3ebdf0…bf30ba`) de `vendor/filters` |
| Estado previo | Ningún registro de UnityCapture/CamVoice en `HKCU` ni en `HKLM` (inventario §4) |

Limitación: la cuenta pertenece al grupo Administradores. Un proceso sin elevar
tiene el mismo acceso al registro que una cuenta estándar (no puede escribir en
`HKLM\Software\Classes`), pero **no se ha probado con una cuenta estándar
real** (§7.3).

## 2. Qué escribe `DllRegisterServer` y por qué no se usa `regsvr32`

Para no adivinar las claves, se capturó lo que escribe el propio filtro sin
tocar el registro real: un proceso carga la DLL, redirige **solo para sí
mismo** `HKCR`, `HKLM` y `HKCU` a claves temporales bajo
`HKCU\Software\camvoice-probe` con `RegOverridePredefKey`, llama a
`DllRegisterServer`, vuelca lo escrito y borra las claves temporales. Para la
DLL de 32 bits se repitió desde `SysWOW64\WindowsPowerShell` (proceso de
32 bits).

Procedimiento reproducible (para validar otra versión del filtro):

1. `CoInitialize`, `LoadLibraryW(dll)`, `GetProcAddress("DllRegisterServer")`.
2. `RegCreateKeyEx(HKCU, "Software\camvoice-probe\{HKCR,HKLM,HKCU}")`.
3. `RegOverridePredefKey(HKEY_CLASSES_ROOT|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER, <clave temporal>)`.
4. Llamar a `DllRegisterServer`, deshacer las redirecciones (`NULL`), volcar y
   borrar `HKCU\Software\camvoice-probe`.

Resultado (hr = `0x00000000` en ambas arquitecturas):

| Escrito en | Clave | Valores |
|---|---|---|
| HKCR | `CLSID\{5C2CD55C-92AD-4999-8666-912BD3E70010}` (32 bits: `…0020`) | `(predeterminado)` = nombre |
| HKCR | `…\InprocServer32` | `(predeterminado)` = ruta de la DLL, `ThreadingModel` = `Both` |
| HKCR | `CLSID\{…0011}` (32 bits: `…0021`) y su `InprocServer32` | «Unity Video Capture Configuration», misma DLL |
| HKCR | `CLSID\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\Instance\{…0010}` | `FriendlyName`, `CLSID`, `FilterData` (88 bytes, REG_BINARY) |
| HKLM | `SOFTWARE\Microsoft\Cryptography\DRM_RNG` | clave vacía, efecto lateral de la inicialización de COM/DirectShow, no del filtro |

`DllRegisterServer` escribe en `HKCR`. Una escritura en `HKCR` de una clave
que no existe ya en `HKCU\Software\Classes` va a `HKLM\Software\Classes`: con
`regsvr32` hace falta administrador y el registro es para todo el equipo. Por
eso **`regsvr32` no se usa en ningún camino** (lo comprueban
`tests/test_filters.py`), y `install-camera` escribe esas mismas claves
directamente en `HKCU\Software\Classes`, en la vista de registro de cada
arquitectura (`KEY_WOW64_64KEY` / `KEY_WOW64_32KEY`). Además añade
`DevicePath = "foo:bar"` en `Instance`, que el filtro intenta escribir con
`IPropertyBag`.

Los CLSID de 64 y 32 bits son **distintos** (`…0010` frente a `…0020`).

## 3. Comandos ejecutados y salida

Todos desde una consola sin elevar, en la raíz del proyecto.

### 3.1 Instalación con el script (`instalacion\1-Instalar.ps1`) — PASA

Código de salida `0`, sin aviso de UAC. Extracto:

```
[3] Camaras ya registradas
         Usuario: EQUIPO\usuario (sin administrador)
         Ninguna.

[4] Registrando la camara como «CamVoice Virtual Camera» (solo para este usuario)
    [ok] Registrada solo para el usuario EQUIPO\usuario, sin permisos de administrador:
              32-bit: C:\Users\usuario\AppData\Local\camvoice\filters\32-bit-aa3ebdf03dea\UnityCaptureFilter32.dll
              64-bit: C:\Users\usuario\AppData\Local\camvoice\filters\64-bit-72812f5363d8\UnityCaptureFilter64.dll
    [ok] DirectShow (64 bits) enumera «CamVoice Virtual Camera».

[5] Verificando en el registro de Windows (HKCU)
    [ok] CamVoice Virtual Camera  (64-bit, este usuario)
    [ok] CamVoice Virtual Camera  (32-bit, este usuario)

[6] Comprobando que DirectShow la enumera y entrega imagen
      [ok] 64-bit: Instalado para este usuario
                SHA-256 72812f5363d8ecb45632253f8c8c888844b1b62e27616f3c8cc21064ccde25e5 (coincide)
      [ok] 32-bit: Instalado para este usuario
                SHA-256 aa3ebdf03dea7f3aab3dd7b724751f49ed71672256b57c6a19aa6809cabf30ba (coincide)
      [ok] 64-bit: un proceso de 64 bits la ve ({5C2CD55C-92AD-4999-8666-912BD3E70010})
      [ok] 32-bit: un proceso de 32 bits la ve ({5C2CD55C-92AD-4999-8666-912BD3E70020})
      [ok] 64-bit: 78/79 muestras con los colores enviados, 8 cambios, 1920x1080
      [!!] 32-bit: No verificable con fotogramas desde este proceso.
         RESULTADO: correcto en todo lo que se pudo verificar.

[7] Diagnostico de la aplicacion
      [ok] Disponible: «CamVoice Virtual Camera» (640x480 a 15 FPS, formato nativo RGBA, backend unitycapture)
         Todo listo.
```

### 3.2 Diagnóstico (`instalacion\3-Diagnostico.ps1`) — PASA

Código de salida `0`; lista las dos entradas como «este usuario» y ninguna del
sistema.

### 3.3 Desinstalación (`instalacion\9-Desinstalar.ps1`), dos veces — PASA

Primera ejecución, código `0`:

```
[4] Quitando el registro de la camara de este usuario
    [!!] Copias en uso por otra aplicación (navegador, Teams...); no se borran ahora. ...
         C:\Users\usuario\AppData\Local\camvoice\filters\64-bit-72812f5363d8
    [ok] 64-bit: registro por usuario quitado
    [ok] 32-bit: registro por usuario quitado
    [ok] DirectShow ya no enumera ninguna cámara de camvoice.
[5] Verificando
    [ok] Tu usuario ya no tiene la camara de camvoice registrada
```

Segunda ejecución, código `0`: «No había ningún registro por usuario de
camvoice». La carpeta de 64 bits se quedó porque `msedge.exe` tenía la DLL
cargada (comprobado con `tasklist /m UnityCaptureFilter64.dll`); el navegador
del usuario no se cerró a propósito. Es el comportamiento previsto (§6.3).

### 3.4 CLI directa — PASA

`camvoice-cli install-camera` dos veces seguidas (idempotente, mismo
inventario byte a byte: `inv_install1` = `inv_install2`), `verify-camera`,
`install-camera --uninstall` dos veces.

### 3.5 Prueba de integración real — PASA

```
$env:CAMVOICE_TEST_REAL_CAMERA = "1"
.venv\Scripts\python.exe -m pytest tests/test_camara_por_usuario_integracion.py -v
5 passed in 5.37s
```

Instala con otro nombre («CamVoice Prueba Integracion»), comprueba que el
registro es de `HKCU`, que DirectShow la enumera en 64 y 32 bits, que entrega
fotogramas con los colores enviados, y desinstala (dos veces).

### 3.6 Ejecutable empaquetado (`dist\camvoice`) — PASA en este equipo

`pyinstaller build/camvoice.spec --noconfirm` termina sin errores (230 MB,
`_internal/filters` con las dos DLL). Con `camvoice-cli.exe`: `install-camera`
→ 0, `verify-camera` → 0 (mismos resultados que §3.1), `--uninstall` dos veces
→ 0. Con los scripts copiados en `dist\camvoice\instalacion`:
`1-Instalar.ps1` → 0 («Encontrada via ejecutable»), `9-Desinstalar.ps1` → 0;
`HKLM` sin cambios. ZIP: `dist\camvoice-win64.zip`, 91 MB.

### 3.7 Pruebas automatizadas — PASA

```
pytest
341 passed, 5 skipped in 18.35s
```

Las 5 omitidas son las de integración real (requieren la variable). Antes de
los cambios la línea base era 300 pasadas y 1 fallo preexistente.

## 4. Inventario del registro, antes y después

Generado con un script que abre `HKCU` y `HKLM` en las vistas de 64 y 32 bits
(`RegistryKey.OpenBaseKey`) y lista las claves de los cuatro CLSID y sus
`Instance`.

**Antes de instalar** (y después de desinstalar, idéntico):

```
LocalMachine/Registry64  Instance:
LocalMachine/Registry32  Instance:
```

La categoría de captura de vídeo de `HKLM` existe y está vacía; en `HKCU` no
hay nada.

**Después de instalar** (resumen; 28 líneas en total):

```
CurrentUser/Registry64  ...\CLSID\{...0010}                  [] = CamVoice Virtual Camera
CurrentUser/Registry64  ...\CLSID\{...0010}\InprocServer32   [] = C:\Users\usuario\AppData\Local\camvoice\filters\64-bit-72812f5363d8\UnityCaptureFilter64.dll
CurrentUser/Registry64  ...\CLSID\{...0010}\InprocServer32   [ThreadingModel] = Both
CurrentUser/Registry64  ...\Instance\{...0010}  [FriendlyName] = CamVoice Virtual Camera, [CLSID], [FilterData] (88 bytes), [DevicePath] = foo:bar
CurrentUser/Registry64  ...\CLSID\{...0011}                  [] = Unity Video Capture Configuration
CurrentUser/Registry32  (lo mismo con {...0020}/{...0021} y UnityCaptureFilter32.dll)
LocalMachine/Registry64  Instance:
LocalMachine/Registry32  Instance:
```

**`HKLM` no cambió** en ninguna de las ejecuciones.

## 5. Consumidores comprobados

| Consumidor | Resultado | Notas |
|---|---|---|
| Enumeración DirectShow propia (`camvoice/output/dshow.py`, `ICreateDevEnum`), 64 bits | **PASA** | Ve la cámara física y «CamVoice Virtual Camera» `{…0010}` |
| Enumeración DirectShow desde un proceso de 32 bits (PowerShell de `SysWOW64`) | **PASA** | `{…0020}` |
| Grafo DirectShow propio + pyvirtualcam, 64 bits | **PASA** | 78/79 muestras con el color enviado, 1920x1080 |
| ffmpeg 7.1 (`-f dshow -list_devices true`) | **PASA** | Lista `@device_sw_{860BB310-…}\{5C2CD55C-…0010}` |
| Microsoft Edge | **PASA (parcial)** | Cargó la DLL desde `%LOCALAPPDATA%\camvoice\filters` (visto con `tasklist /m`); no se evaluó la imagen en una página |
| OpenCV 4.10 `CAP_DSHOW` | **FALLA** | `VideoCapture(1, CAP_DSHOW)` no abre el índice, aunque abre la cámara física. No se ha comprobado si con el registro de sistema ocurre igual; por eso la verificación usa un grafo DirectShow propio y no OpenCV |

## 6. Riesgos y límites conocidos

### 6.1 Aplicaciones elevadas

COM ignora `HKCU\Software\Classes` en procesos elevados. Una aplicación
abierta con «Ejecutar como administrador» **no verá la cámara**. Los scripts
avisan si se ejecutan elevados (`Write-AvisoElevado`) y `verify-camera` muestra
«proceso elevado: sí/no». No hay solución sin escribir en `HKLM`, y no se
ofrece.

### 6.2 Aplicaciones solo Media Foundation

Un filtro DirectShow no aparece en aplicaciones que solo enumeran con Media
Foundation (cámaras de Windows 11 «Frame Server»). Es una limitación del filtro
UnityCapture, no del registro por usuario. **No se ha comprobado** qué hace el
nuevo Teams (§7.1).

### 6.3 DLL en uso

Si una aplicación tiene la DLL cargada, al desinstalar o actualizar se quita el
registro igualmente y la carpeta se borra en la siguiente instalación o
desinstalación. Las copias van en carpetas con el hash (`64-bit-72812f5363d8`),
así que una versión nueva nunca sobrescribe una DLL cargada.

### 6.4 Conflictos

Si el mismo CLSID ya está registrado **para todo el equipo** (`HKLM`, por
ejemplo por un instalador de UnityCapture o una versión antigua de camvoice
instalada con `regsvr32`) o en `HKCU` por otro programa (otra DLL):

- `install-camera` **no escribe nada** y sale con código `2`;
- lo muestra como «Registro existente del sistema (solo lectura)»;
- `--uninstall` no lo toca.

Qué hacer: el registro existente ya hace aparecer la cámara, con el nombre que
le diera quien la registró; `camvoice-cli cameras` lo lista y la aplicación
puede intentar usarlo (no se ha probado en este caso, §7.4). Quitar un registro de todo el equipo queda fuera de
camvoice: no lo hace, no pide permisos para hacerlo ni lo propone; es una
decisión de quien administre ese equipo.

Este caso está cubierto por pruebas con un registro simulado
(`test_filters.py`, `test_camara_cli.py`), **no en una máquina real** (§7.4).

### 6.5 Otros usuarios

Cada usuario del equipo la instala en su sesión. Un usuario no ve la cámara de
otro.

## 7. Pruebas NO EJECUTADAS

### 7.1 Teams, Meet, Zoom y Discord — NO EJECUTADA

Procedimiento, para cada aplicación:

1. Sesión sin elevar. `camvoice-cli install-camera` y `verify-camera`.
2. `camvoice` → Iniciar con «Enviar a la cámara virtual».
3. Cerrar la aplicación por completo (también del área de notificación) y
   abrirla.
4. Ajustes → Vídeo / Cámara: ¿aparece «CamVoice Virtual Camera»? ¿Se ve la
   imagen y cambia al hablar?
5. Anotar versión de la aplicación y si es de escritorio o web (Meet en Chrome
   y en Edge).

| Aplicación | Aparece | Imagen | Versión | Resultado |
|---|---|---|---|---|
| Teams (nuevo) | | | | NO EJECUTADA |
| Teams (clásico) | | | | NO EJECUTADA |
| Google Meet en Chrome | | | | NO EJECUTADA |
| Google Meet en Edge | | | | NO EJECUTADA |
| Zoom | | | | NO EJECUTADA |
| Discord | | | | NO EJECUTADA |

### 7.2 Fotogramas a 32 bits — NO EJECUTADA

La enumeración de 32 bits sí está probada; la imagen no, porque el emisor
(pyvirtualcam) y el lector de la prueba son de 64 bits. Procedimiento: con
camvoice enviando, abrir una aplicación de 32 bits que use DirectShow (por
ejemplo `ffmpeg` de 32 bits: `ffmpeg -f dshow -i video="CamVoice Virtual Camera"
-frames:v 30 salida.mp4`) y comprobar que la imagen es la enviada.

### 7.3 Cuenta estándar (sin pertenencia a Administradores) — NO EJECUTADA

Procedimiento: crear una cuenta local estándar, iniciar sesión con ella,
descomprimir el ZIP en su perfil o en `D:\`, ejecutar `INSTALAR.bat`,
`camvoice-cli verify-camera`, `DESINSTALAR.bat`. Debe dar los mismos
resultados que §3 sin ningún aviso de UAC, e inventario de `HKLM` sin cambios.

### 7.4 Conflicto real con un registro de sistema — NO EJECUTADA

Requiere registrar UnityCapture con `regsvr32` como administrador, lo que este
trabajo no hace. Procedimiento, en una máquina de pruebas: `regsvr32
UnityCaptureFilter64.dll` como administrador; luego, sin elevar,
`camvoice-cli install-camera` debe salir con `2` sin cambiar `HKCU`; deshacer
con `regsvr32 /u`.

### 7.5 Ejecutable empaquetado en una máquina limpia — NO EJECUTADA

El paquete se probó en esta máquina (§3.6), que tiene Python. Procedimiento:
copiar `dist\camvoice-win64.zip` a una máquina sin Python, descomprimir y
repetir §3.1 y §3.3 con `INSTALAR.bat` y `DESINSTALAR.bat`.

## 8. Criterios

| Criterio | Resultado | Evidencia |
|---|---|---|
| Instala sin UAC ni administrador | **PASA** | §3.1, cuenta sin elevar |
| Binarios en `%LOCALAPPDATA%\camvoice`, configuración en `%APPDATA%\camvoice` | **PASA** | §3.1, `doctor` |
| Ninguna escritura en `HKLM` ni directa en `HKCR` | **PASA** | §4; `_Registry` rechaza `HKLM` (prueba); sin `regsvr32`/`runas` (pruebas de código y scripts) |
| Registro en 64 y 32 bits | **PASA** | §4 |
| DirectShow la enumera en 64 y 32 bits | **PASA** | §3.1, §3.5, §5 |
| Entrega fotogramas | **PASA en 64 bits**; 32 bits NO EJECUTADA | §3.1, §7.2 |
| Instalar y desinstalar son idempotentes | **PASA** | §3.3, §3.4, pruebas |
| Reversión ante fallo | **PASA (simulado)** | `test_filters.py`, fallos en las escrituras 0/1/4/7 |
| Conflicto con `HKLM` detectado y respetado | **PASA (simulado)**; real NO EJECUTADA | §6.4, §7.4 |
| Desinstalación limpia | **PASA** | §3.3, §4 |
| Funciona en Teams/Meet/Zoom/Discord | **NO EJECUTADA** | §7.1 |
| Funciona con una cuenta estándar | **NO EJECUTADA** | §7.3 |
