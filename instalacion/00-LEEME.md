# Instalación rápida — orden de ejecución

Scripts para poner camvoice a funcionar en una máquina nueva y probarlo en
cinco minutos, sin grabar ningún video.

**Están numerados por el orden en que se ejecutan.** Cada `.ps1` tiene un `.bat`
del mismo nombre para abrirlo con doble clic.

---

## Orden

| # | Doble clic | Script | Qué hace | ¿Administrador? |
|---|---|---|---|---|
| **1** | `INSTALAR.bat` | `1-Instalar.ps1` | Comprueba la máquina y registra la cámara virtual **para tu usuario** | No |
| **2** | `PROBAR.bat` | `2-ProbarRapido.ps1` | Genera clips de prueba y emite a la cámara | No |
| **3** | `DIAGNOSTICO.bat` | `3-Diagnostico.ps1` | Mira qué falla. No cambia nada | No |
| **9** | `DESINSTALAR.bat` | `9-Desinstalar.ps1` | Quita la cámara de tu usuario | No |

Ningún script pide permisos de administrador ni muestra el aviso de UAC. La
cámara se registra solo para el usuario con la sesión abierta: en el registro
de ese usuario (`HKCU`) y con las DLL copiadas a `%LOCALAPPDATA%\camvoice\filters`.
No se escribe nada en `HKLM`, `Program Files` ni `System32`, y otros usuarios
del equipo no ven la cámara (cada uno puede instalarla en su sesión).

**No los ejecutes con «Ejecutar como administrador».** No hace falta, y las
aplicaciones elevadas no leen el registro por usuario: la verificación fallaría
aunque Teams o el navegador sí vean la cámara.

El paso **3 solo hace falta si algo va mal**. Con 1 y 2 basta para probarlo.

---

## El camino corto

```
1. Doble clic en  INSTALAR.bat     -> no pide permisos; tarda unos segundos
2. Doble clic en  PROBAR.bat       -> abre el navegador y elige la cámara
```

En el paso 2 se abre `webcamtests.com` solo, esperas 8 segundos para elegir
**CamVoice Virtual Camera** en la lista, y verás la imagen moverse con tu voz.

Si llegas hasta ahí, **la cadena completa funciona** y ya puedes grabar tus
clips reales.

---

## Qué hace cada uno con detalle

### 1-Instalar.ps1

```powershell
.\1-Instalar.ps1
.\1-Instalar.ps1 -Nombre "Mi cámara"     # otro nombre visible
```

1. Comprueba Windows 10/11 de 64 bits, RAM, espacio en disco y audio.
2. Localiza la aplicación (ejecutable empaquetado o árbol de código).
3. Lista lo que ya estaba registrado, distinguiendo *este usuario* (HKCU) de
   *sistema* (HKLM, solo lectura).
4. Registra la cámara en **64 y 32 bits** solo para tu usuario
   (`camvoice-cli install-camera`).
5. **Verifica leyendo el registro de Windows directamente**, no fiándose de lo
   que la aplicación diga haber hecho.
6. Comprueba que DirectShow la enumera en 64 y 32 bits y que entrega imagen
   (`camvoice-cli verify-camera`).
7. Ejecuta el diagnóstico completo.

Es idempotente: se puede repetir sin romper nada. Si algo falla a mitad, deshace
lo que llegó a escribir. Códigos de salida: `0` correcto, `1` error, `2` ya hay
un registro de este filtro **del sistema o de otro programa** (no se toca ni se
oculta; ver `docs\VALIDACION_INSTALACION_USUARIO.md`).

### 2-ProbarRapido.ps1

```powershell
.\2-ProbarRapido.ps1                # prueba completa, 25 s
.\2-ProbarRapido.ps1 -Segundos 60
.\2-ProbarRapido.ps1 -SoloVad       # solo el detector de voz, con guion
```

El modo `-SoloVad` es el que se usa para **ajustar la sensibilidad** del
micrófono: muestra nivel, piso de ruido y cada cambio de estado con marca de
tiempo, y trae el guion de la prueba en pantalla.

### 3-Diagnostico.ps1

```powershell
.\3-Diagnostico.ps1
.\3-Diagnostico.ps1 -Guardar        # vuelca todo a %LOCALAPPDATA%\camvoice\diagnosticos\
```

No modifica nada. Reporta máquina, cámaras registradas (las dos arquitecturas),
procesos que podrían estar ocupando el filtro, micrófonos y espacio ocupado.
Usa `-Guardar` para generar el archivo que adjuntar al reportar un problema.

### 9-Desinstalar.ps1

```powershell
.\9-Desinstalar.ps1
.\9-Desinstalar.ps1 -BorrarDatos    # también configuración y caché
```

Quita solo el registro por usuario de camvoice y sus DLL de
`%LOCALAPPDATA%\camvoice\filters`. Un registro del sistema o de otro programa
se informa y no se toca. Se puede repetir: la segunda vez no hace nada.

> **Ejecútalo antes de borrar la carpeta del programa**, porque es quien sabe
> limpiar el registro. Las DLL registradas están en tu perfil, así que borrar
> la carpeta del programa no deja la cámara rota, pero sí registrada.

Si Teams, el navegador u otra aplicación tienen la cámara abierta, la DLL está
en uso y no se puede borrar en ese momento: el registro se quita igual y el
aviso indica la carpeta, que se limpia en la siguiente instalación o
desinstalación con esa aplicación cerrada.

---

## Si Windows bloquea los scripts

Los `.bat` ya lo resuelven: llaman a PowerShell con `-ExecutionPolicy Bypass`,
que afecta solo a esa invocación y **no cambia la configuración del equipo**.

Si prefieres lanzar el `.ps1` directamente y sale *«la ejecución de scripts está
deshabilitada en este sistema»*:

```powershell
powershell -ExecutionPolicy Bypass -File .\1-Instalar.ps1
```

## Si SmartScreen avisa

El ejecutable **no está firmado digitalmente**, así que la primera vez Windows
puede decir «Windows protegió su PC»: *Más información → Ejecutar de todas
formas*. No afecta al funcionamiento. Para un despliegue en una organización
haría falta un certificado de firma de código.

---

## Si no tienes el ejecutable

Estos scripts buscan `camvoice-cli.exe` junto a ellos, un nivel por encima o en
`dist\camvoice\`. Si no lo encuentran, caen al código fuente usando `.venv`.

Para compilarlo, desde la raíz del proyecto:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\scripts\build-portable.ps1
```

Queda en `dist\camvoice\camvoice.exe` (con esta carpeta copiada dentro, en
`dist\camvoice\instalacion\`) y el ZIP en `dist\camvoice-win64.zip`. Los
scripts lo encontrarán solo. O descárgalo ya compilado con
`OBTENER-CAMVOICE.bat` desde la raíz del repositorio.

---

## Dónde está cada cosa

| | |
|---|---|
| Requisitos completos | `..\docs\REQUISITOS.md` |
| Instalación y configuración a mano | `..\docs\INSTALACION.md` |
| Cómo funciona la cámara virtual | `..\docs\CAMARA-VIRTUAL.md` |
| Procedimiento de pruebas | `..\docs\PRUEBAS.md` |

Lo que los scripts automatizan se puede hacer a mano con:

```
camvoice-cli install-camera
camvoice-cli verify-camera
camvoice-cli cameras
camvoice-cli doctor
camvoice-cli make-clips --outdir clips
camvoice-cli vad-test --duration 30
camvoice-cli install-camera --uninstall
```
