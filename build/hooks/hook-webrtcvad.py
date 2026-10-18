"""Hook de PyInstaller que reemplaza al que trae pyinstaller-hooks-contrib.

El hook estándar hace `copy_metadata('webrtcvad')` y falla con
`PackageNotFoundError`, porque el paquete que instalamos es `webrtcvad-wheels`:
el **módulo** se llama `webrtcvad` pero los **metadatos** están bajo
`webrtcvad-wheels`. Es consecuencia directa —y necesaria— de usar la variante
con rueda precompilada para Windows en lugar de la que exige compilador.

Los directorios de hooks propios se consultan antes que los de contrib, así que
este gana. Se prueban los dos nombres y, si ninguno está, se sigue sin
metadatos: `webrtcvad` no los lee en tiempo de ejecución.
"""

from PyInstaller.utils.hooks import copy_metadata

datas = []
for distribution in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas = copy_metadata(distribution)
    except Exception:
        continue
    break
