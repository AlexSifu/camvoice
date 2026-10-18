# La cámara virtual: independiente de OBS

**Resuelto. OBS ya no hace falta.** La aplicación registra su propio filtro,
**solo para el usuario actual y sin permisos de administrador**, y aparece como
**«CamVoice Virtual Camera»** para las aplicaciones DirectShow.

Verificado en este equipo el 2026-10-18, en una sesión sin elevar:

```
camvoice-cli install-camera
  [ok] Registrada solo para el usuario EQUIPO\usuario, sin permisos de administrador:
       32-bit: C:\Users\usuario\AppData\Local\camvoice\filters\32-bit-aa3ebdf03dea\UnityCaptureFilter32.dll
       64-bit: C:\Users\usuario\AppData\Local\camvoice\filters\64-bit-72812f5363d8\UnityCaptureFilter64.dll
  [ok] DirectShow (64 bits) enumera «CamVoice Virtual Camera».

camvoice-cli verify-camera
  [ok] 64-bit: un proceso de 64 bits la ve ({5C2CD55C-92AD-4999-8666-912BD3E70010})
  [ok] 32-bit: un proceso de 32 bits la ve ({5C2CD55C-92AD-4999-8666-912BD3E70020})
  [ok] 64-bit: 78/79 muestras con los colores enviados, 8 cambios, 1920x1080
```

Qué está probado y qué no (Teams, Meet, Zoom y Discord **no** se han probado
todavía) está en [VALIDACION_INSTALACION_USUARIO.md](VALIDACION_INSTALACION_USUARIO.md).

Con el registro anterior (para todo el equipo), alimentándola 60 s: **15,03 FPS,
9,4 % de un núcleo, memoria estable**. El filtro y el envío no han cambiado.

---

## 1. Por qué hace falta registrar algo

Cuando eliges una cámara en Teams, Teams **no habla con otro programa**. Carga
una DLL registrada en el sistema **dentro de su propio proceso** y le pide
fotogramas.

```
  ┌──────────────────────────┐        ┌───────────────────────────────┐
  │  Proceso de Teams        │        │  Proceso de camvoice          │
  │                          │        │                               │
  │  ┌────────────────────┐  │        │  ┌─────────────────────────┐  │
  │  │ el filtro .dll     │◄─┼────────┼──┤ motor de render         │  │
  │  │ (cargado por Teams)│  │ memoria│  │ (Python)                │  │
  │  └────────────────────┘  │compart.│  └─────────────────────────┘  │
  └──────────────────────────┘        └───────────────────────────────┘
          la "cámara"                        quien la alimenta
```

De ahí se sigue algo que no se puede sortear:

> **Ninguna aplicación en Python puede ser, por sí misma, una cámara de
> Windows.** Hace falta una DLL nativa registrada en el sistema.

Lo que sí puede hacer Python es **alimentarla** por memoria compartida. Por eso
la pregunta nunca fue «¿con DLL o sin DLL?», sino **qué DLL y quién la
registra**. La respuesta ahora es: la nuestra, y la registra nuestro instalador
en el perfil del usuario, sin administrador.

### Por qué DirectShow y no Media Foundation

| | DirectShow (elegido) | Media Foundation |
|---|---|---|
| Estado | Antiguo, pero universal en la práctica | Moderno, Windows 11 22H2+ |
| Lo ven | Teams, Zoom, Meet, Discord, Chrome, Edge, OBS | Lo anterior **más** la app Cámara de Windows |
| Firma de código | **No hace falta** | Necesaria, y en la práctica empaquetado MSIX |
| Instalación | Claves COM en el registro **del usuario** (`HKCU`), sin administrador | Paquete firmado |

DirectShow cubre todas las aplicaciones de videollamada que importan y no exige
comprar un certificado de firma. La única pérdida es la app **Cámara de
Windows**, que no sirve de todos modos para comprobar nada útil.

---

## 2. Qué se usa y por qué

El filtro es **UnityCaptureFilter**, de
[github.com/schellingb/UnityCapture](https://github.com/schellingb/UnityCapture),
con **licencia MIT**. Pese al nombre, **no requiere ni Unity ni OBS**: es un
filtro DirectShow autónomo de propósito general.

Se incluyen las dos arquitecturas en `vendor/filters/`:

| Archivo | Arquitectura | Tamaño |
|---|---|---|
| `UnityCaptureFilter64.dll` | x64 | 157.696 bytes |
| `UnityCaptureFilter32.dll` | x86 | 168.448 bytes |

**Las dos hacen falta.** Un filtro DirectShow solo se carga en procesos de su
misma arquitectura: una aplicación de videollamada de 32 bits **únicamente**
verá el filtro de 32 bits.

### El detalle que lo cambió todo: el nombre es un parámetro

El plan inicial contemplaba compilar un filtro propio solo para que la cámara no
se llamara «Unity Video Capture». Resultó innecesario: el nombre visible es solo
el valor `FriendlyName` de la entrada del registro, y el filtro lo lee de ahí.

Eso elimina la única razón de peso para mantener un filtro compilado a medida, y
con ella la necesidad de un compilador de C++.

### Por qué no se usa `regsvr32`

`DllRegisterServer` de UnityCapture escribe en `HKLM` (llama a
`IFilterMapper2::RegisterFilter`, que registra para todo el equipo), así que
`regsvr32` exige administrador. En su lugar, `install-camera` escribe **a mano,
en `HKCU\Software\Classes`**, exactamente las mismas claves que esa función
escribiría, capturadas una vez de una ejecución real:

| Clave (bajo `HKCU\Software\Classes\CLSID`) | Valores |
|---|---|
| `{filtro}` | (predeterminado) = nombre de la cámara |
| `{filtro}\InprocServer32` | (predeterminado) = ruta de la DLL; `ThreadingModel` = `Both` |
| `{configuración}` | (predeterminado) = `Unity Video Capture Configuration` |
| `{configuración}\InprocServer32` | igual que el del filtro |
| `{860BB310-5D01-11D0-BD3B-00A0C911CE86}\Instance\{filtro}` | `FriendlyName`, `CLSID`, `FilterData` (binario, 88 bytes), `DevicePath` = `foo:bar` |

COM y el enumerador de dispositivos de DirectShow leen `HKCR`, que es la vista
combinada de `HKCU\Software\Classes` sobre `HKLM\Software\Classes`, así que una
aplicación del usuario ve la cámara igual que si estuviera registrada para el
equipo. Cada arquitectura usa su CLSID (`…0010` en 64 bits, `…0020` en 32) y se
escribe en su vista del registro (`KEY_WOW64_64KEY` / `KEY_WOW64_32KEY`).

El procedimiento de captura y la evidencia están en
[VALIDACION_INSTALACION_USUARIO.md](VALIDACION_INSTALACION_USUARIO.md) §2.

---

## 3. Uso

```bash
camvoice-cli install-camera                 # registra como «CamVoice Virtual Camera»
camvoice-cli install-camera --name "Mi cámara"   # con otro nombre
camvoice-cli verify-camera                  # registro, enumeración 64/32 y fotogramas
camvoice-cli cameras                        # qué hay registrado (usuario / sistema)
camvoice-cli install-camera --uninstall     # quita el registro de tu usuario
```

`install-camera` **no pide administrador ni muestra UAC**. Copia las DLL a
`%LOCALAPPDATA%\camvoice\filters\` y registra la cámara solo para el usuario con
la sesión abierta. Otros usuarios del equipo no la ven; cada uno puede
instalarla en su sesión.

| Código de salida | `install-camera` | `verify-camera` |
|---|---|---|
| `0` | Registrada y enumerada por DirectShow (64 bits) | Nada falló |
| `1` | Error (sin cambios: lo escrito se deshace) | Alguna comprobación falló |
| `2` | Conflicto: ya hay un registro del sistema o de otro programa; no se toca | — |

Límites conocidos de un registro por usuario:

- **Aplicaciones elevadas** («Ejecutar como administrador»): COM no lee `HKCU`
  en procesos elevados, así que no verán la cámara.
- **Si el filtro ya está registrado para todo el equipo** (`HKLM`, por ejemplo
  por otra instalación de UnityCapture), camvoice no lo modifica ni lo oculta:
  `install-camera` sale con `2` y la UI lo muestra como «Registro existente del
  sistema (solo lectura)».
- **Aplicaciones que solo usan Media Foundation** no ven filtros DirectShow,
  estén registrados donde estén.

Después de registrar, **cierra y vuelve a abrir** Teams, Zoom o el navegador:
enumeran las cámaras al arrancar y no se enteran de una nueva a mitad de sesión.

### Selección automática

`output.backend` vale `"auto"` por defecto. La aplicación consulta el registro
de Windows y **prefiere el filtro que no depende de OBS**:

```
backend elegido por auto: ['unitycapture', 'obs']
```

Con eso, en un equipo que tenga los dos registrados usa el propio y no compite
con OBS por el dispositivo. Si solo hay OBS, lo usa y funciona igual.

---

## 4. Lo que está implementado

En `camvoice/output/filters.py` y `camvoice/output/sink.py`:

- **Enumeración real del registro de Windows**, en `HKCU` y `HKLM` y en las
  vistas de 64 y 32 bits, con ámbito (usuario / sistema), CLSID, ruta de la DLL
  y si esa DLL todavía existe (detecta registros huérfanos).
- **Enumeración real de DirectShow** (`camvoice/output/dshow.py`): lo que ve un
  proceso de 64 bits, y uno de 32 bits a través del PowerShell de `SysWOW64`.
- **Prueba de fotogramas** de punta a punta: envía colores con pyvirtualcam y
  los lee con un grafo DirectShow nativo.
- **Selección automática de backend** que prefiere el filtro independiente.
- **Registro y desregistro por usuario**, sin elevación, idempotentes, con
  verificación del hash de cada DLL y rollback si algo falla a mitad.
- **Aviso si falta una de las dos arquitecturas.**
- **Mensajes de error que dicen qué hacer**, distinguiendo «no hay filtro» de
  «el filtro está ocupado por otra aplicación».
- **Pruebas** en `tests/test_filters.py` y `tests/test_camara_cli.py`, que no
  modifican el sistema (registro en memoria), y una prueba de integración real
  opcional en `tests/test_camara_por_usuario_integracion.py`.

El `.spec` de PyInstaller empaqueta `vendor/filters/` en la subcarpeta
`filters/` del ejecutable, donde `filters_dir()` la busca con `sys._MEIPASS`.
Desde ahí `install-camera` las copia al perfil del usuario antes de registrarlas.

---

## 5. Rendimiento con este filtro

El formato nativo de UnityCapture es **RGBA**, no NV12, así que hay una
conversión por fotograma que con el filtro de OBS no existía. Medido:

| Formato interno | Coste de conversión |
|---|---|
| NV12 → RGBA | 0,003 ms por fotograma |
| BGR → RGBA | 0,004 ms por fotograma |

Frente a los 66,7 ms de presupuesto a 15 FPS, la diferencia es irrelevante, así
que **se mantiene NV12**: ocupa la mitad en la caché de fotogramas y conserva la
ventaja con el backend de OBS.

Comparativa de 60 s con los dos filtros, mismo equipo y configuración:

| | Filtro de OBS | CamVoice (UnityCapture) |
|---|---|---|
| FPS efectivos | 15,02 | 15,03 |
| CPU (% de un núcleo) | 10,6 % | 9,4 % |
| Coste por fotograma (p95) | 2,05 ms | 5,03 ms |
| Memoria | estable | estable |

Los 3 ms adicionales de p95 son el coste real de la conversión a RGBA bajo
carga. Sigue siendo el 7,5 % del presupuesto.

---

## 6. Licencias

`UnityCaptureFilter` es **MIT**: se puede redistribuir sin restricciones, en
producto abierto o cerrado. Ver `docs/LICENCIAS.md` §3.

Nótese que esto **mejora** la situación de licencia del proyecto: antes la ruta
por defecto dependía de OBS Studio (GPL-2.0). La dependencia que queda bajo
GPL-2.0 es `pyvirtualcam`, y `output/sink.py` la aísla detrás del protocolo
`FrameSink` por si hubiera que sustituirla.

---

## 7. Si algún día hace falta un filtro compilado a medida

Ya no es necesario para el nombre, pero seguiría siendo la vía para:

- **CLSID propio**, de modo que no colisione con una instalación de UnityCapture
  hecha por otro programa.
- **Formato nativo NV12**, que quitaría la conversión por fotograma.
- **Varios dispositivos** simultáneos.

Haría falta instalar en Visual Studio 2022 el workload «Desarrollo de escritorio
con C++» (comprobado: está Visual Studio pero sin compilador ni SDK), partir del
código MIT de UnityCapture y compilar en x64 y x86. El código Python no
cambiaría: ya detecta cualquier filtro registrado.

**No lo recomiendo salvo que aparezca una de esas tres necesidades.**
