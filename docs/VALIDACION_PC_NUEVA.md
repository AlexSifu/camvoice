# Validación en una PC nueva

Procedimiento para comprobar camvoice en un equipo que nunca lo ha tenido, con
una cuenta de usuario **estándar** (sin administrador). Ningún paso pide UAC ni
escribe en `HKLM`, `Program Files` o `System32`.

Rellena la tabla del final y guarda el diagnóstico (paso 8): es lo que hace
falta para dar por buena la prueba o para reportar un fallo.

## 0. Requisitos del equipo

- Windows 10 u 11, **64 bits**.
- Unos 300 MB libres en el perfil del usuario.
- Un micrófono (para la prueba de voz; registrar la cámara no lo necesita).
- Conexión a Internet solo para descargar; camvoice funciona sin ella.

Comprueba el tipo de cuenta: **Configuración → Cuentas → Tu información**
debe decir «Usuario estándar». Una cuenta administradora, aunque no eleve, no
cuenta como prueba de cuenta estándar.

## 1. Obtener el repositorio

Con Git:

```powershell
git clone https://github.com/AlexSifu/camvoice.git
cd camvoice
```

Sin Git: en la página del repositorio, **Code → Download ZIP**, descomprime y
abre la carpeta.

## 2a. Obtener la versión portable (sin Python)

Doble clic en **`OBTENER-CAMVOICE.bat`**, en la raíz del clon. Descarga
`camvoice-win64.zip` del último Release, comprueba su SHA-256, lo descomprime
en `%LOCALAPPDATA%\Programs\camvoice\<versión>\` y lanza la instalación de la
cámara (paso 3).

Alternativa manual: descarga `camvoice-win64.zip` y `camvoice-win64.zip.sha256`
de <https://github.com/AlexSifu/camvoice/releases/latest>, compara

```powershell
(Get-FileHash camvoice-win64.zip -Algorithm SHA256).Hash
Get-Content camvoice-win64.zip.sha256
```

y descomprime en una carpeta de tu usuario (no en `Program Files`). Si Windows
marca los archivos como descargados de Internet:
`Get-ChildItem -Recurse <carpeta> | Unblock-File`.

Si `OBTENER-CAMVOICE.bat` responde que **no hay ninguna versión publicada**,
sigue el 2b.

## 2b. Compilarla desde el código (alternativa)

Necesita Python 3.12 x64 instalado **para el usuario actual** (el instalador
de python.org lo permite sin administrador: desmarca «for all users»).

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\scripts\build-portable.ps1
```

Si PowerShell bloquea el script:
`powershell -ExecutionPolicy Bypass -File .\scripts\build-portable.ps1`.
El resultado queda en `dist\camvoice\` y `dist\camvoice-win64.zip`.

## 3. Instalar la cámara para tu usuario

En la carpeta descomprimida (o `dist\camvoice\`): **`instalacion\INSTALAR.bat`**.

Esperado:

- No aparece ningún aviso de UAC.
- Termina con el resultado correcto y código de salida `0`.
- Código `2`: ya existe un registro del filtro **del sistema o de otro
  programa**. No se toca; anótalo y adjunta el diagnóstico (paso 8).

## 4. Verificar

```powershell
.\camvoice-cli.exe verify-camera
```

Debe confirmar el registro en `HKCU` (64 y 32 bits), que DirectShow enumera
**CamVoice Virtual Camera** y que se reciben fotogramas.

## 5. Probar con una aplicación

1. `instalacion\PROBAR.bat`: genera clips de prueba, emite a la cámara y abre
   `webcamtests.com`.
2. En el navegador, elige **CamVoice Virtual Camera**.
3. Habla y calla: la imagen debe cambiar entre los dos clips.

Después, si los tienes, prueba en la aplicación real: elige la cámara en su
configuración de vídeo con `camvoice.exe` abierto y emitiendo.

## 6. Abrir la interfaz

Doble clic en **`camvoice.exe`**. SmartScreen puede avisar porque el ejecutable
no está firmado: *Más información → Ejecutar de todas formas*.

## 7. Desinstalar

**`instalacion\DESINSTALAR.bat`** (antes de borrar la carpeta). Cierra antes
las aplicaciones que usen la cámara; si alguna la tiene abierta, el registro se
quita igual y la DLL se limpia en la próxima ejecución. Repetirlo no debe hacer
nada. Después puedes borrar la carpeta del programa.

## 8. Guardar el diagnóstico

```powershell
powershell -ExecutionPolicy Bypass -File .\instalacion\3-Diagnostico.ps1 -Guardar
```

Queda en `%LOCALAPPDATA%\camvoice\diagnosticos\diagnostico-<fecha>.txt`, dentro
de tu perfil. Adjúntalo al reportar el resultado. No contiene audio ni vídeo.

## Registro de la prueba

| Comprobación | Resultado | Notas |
|---|---|---|
| Windows y versión (`winver`) | | |
| Cuenta estándar (no administradora) | | |
| Origen: Release / compilado | | |
| SHA-256 verificado | | |
| `INSTALAR.bat` sin UAC, código 0 | | |
| `verify-camera` correcto | | |
| Navegador muestra la cámara y cambia con la voz | | |
| Teams | | |
| Google Meet | | |
| Zoom | | |
| Discord | | |
| Aplicación de 32 bits | | |
| `DESINSTALAR.bat` sin UAC; la cámara deja de aparecer | | |
| Diagnóstico guardado | | |

Hasta que esta tabla esté rellena en un equipo distinto del de desarrollo y con
una cuenta estándar, la instalación en una PC nueva **no está validada**.
