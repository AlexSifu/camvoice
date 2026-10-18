# Especificación de PyInstaller para camvoice.
#
# Uso, desde la raíz del proyecto y con el entorno virtual activado:
#     pyinstaller build/camvoice.spec --noconfirm
# El resultado queda en dist/camvoice/ (carpeta) o dist/camvoice.exe (un archivo,
# ver ONE_FILE más abajo).
#
# Decisiones de empaquetado:
#
#  * Se excluyen los módulos de Qt que no se usan. PySide6-Essentials ya es la
#    variante reducida, pero WebEngine, Quick, 3D y los traductores siguen
#    pesando mucho y no se abre ninguno.
#  * Se excluye `tkinter`, `matplotlib` y `scipy` por si alguna dependencia los
#    arrastrara: nada del proyecto los usa.
#  * `console=False` para que el ejecutable no abra una consola al hacer doble
#    clic. El modo consola sigue disponible lanzándolo desde una terminal con
#    subcomandos (`camvoice.exe doctor`), porque la salida se escribe igual.
#  * NO se empaqueta la DLL de la cámara virtual de OBS. Las del filtro propio
#    (vendor/filters) sí, pero solo como origen: `install-camera` las copia a
#    %LOCALAPPDATA%\camvoice\filters y las registra en HKCU, sin administrador.
#    Ver docs/CAMARA-VIRTUAL.md.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ONE_FILE = False
"""Un solo .exe es más cómodo de distribuir pero arranca más lento: descomprime
~150 MB a una carpeta temporal en cada ejecución. La carpeta arranca al
instante. Para un instalador, la carpeta es la opción correcta."""

PROJECT = Path(SPECPATH).parent
sys.path.insert(0, str(PROJECT))

EXCLUDES = [
    # Qt que no se usa
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    # Nada del proyecto los usa
    "tkinter", "matplotlib", "scipy", "pandas", "PIL", "IPython", "pytest",
    "setuptools", "pip",
]

# Todas las dependencias pesadas se importan dentro de las funciones que las
# necesitan, para que `camvoice doctor` arranque sin cargar Qt ni OpenCV. Ese
# estilo es exactamente lo que el análisis estático de PyInstaller no sigue: sin
# declararlas aquí, el ejecutable se construye sin ninguna de ellas y falla al
# arrancar. Comprobado: la primera versión de este .spec produjo un ejecutable
# de 20 MB sin Qt, sin OpenCV y sin NumPy.
HIDDEN = [
    *collect_submodules("camvoice"),
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "cv2",
    "numpy",
    "sounddevice",
    # sounddevice carga PortAudio por cffi en tiempo de ejecución.
    "_sounddevice_data",
    "cffi",
    "_cffi_backend",
    "pyvirtualcam",
    "psutil",
    "webrtcvad",
]

# Runtime de C++ de Microsoft que necesita Qt y que PyInstaller NO empaqueta.
#
# PyInstaller incluye VCRUNTIME140*.dll y ucrtbase.dll, pero no la biblioteca
# estándar de C++ (MSVCP140*.dll), que Qt6Core, Qt6Gui y Qt6Widgets importan.
# En una máquina de desarrollo esto no se nota porque Visual Studio o cualquier
# otro programa ya las dejó en System32; en un Windows limpio **la ventana no
# abre**, con un error de DLL que no apunta a la causa.
#
# Microsoft permite explícitamente la distribución "app-local" de estas DLL
# (copiarlas junto al ejecutable), que es lo que se hace aquí. La alternativa
# sería exigir al usuario instalar el Visual C++ Redistributable, y eso
# convierte una copia-y-ejecuta en una instalación con requisitos previos.
_VC_RUNTIME = ["MSVCP140.dll", "MSVCP140_1.dll", "MSVCP140_2.dll"]
_SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"

VC_BINARIES = []
_missing_runtime = []
for _name in _VC_RUNTIME:
    _src = _SYSTEM32 / _name
    if _src.exists():
        VC_BINARIES.append((str(_src), "."))
    else:
        _missing_runtime.append(_name)
if _missing_runtime:
    print(
        "ADVERTENCIA de empaquetado: no se encontraron "
        + ", ".join(_missing_runtime)
        + " en System32. El ejecutable solo arrancará en equipos que ya tengan "
        "el Visual C++ Redistributable instalado."
    )

a = Analysis(
    [str(PROJECT / "camvoice" / "__main__.py")],
    pathex=[str(PROJECT)],
    binaries=VC_BINARIES,
    # Las DLL del filtro de cámara virtual, si están presentes. Se publican
    # en la subcarpeta "filters", que es donde las busca
    # camvoice.output.filters.filters_dir() al correr empaquetado; desde ahí
    # install-camera las copia al perfil del usuario antes de registrarlas.
    datas=(
        [(str(PROJECT / "vendor" / "filters"), "filters")]
        if (PROJECT / "vendor" / "filters").is_dir()
        else []
    ),
    hiddenimports=HIDDEN,
    # Los hooks propios se consultan antes que los de pyinstaller-hooks-contrib.
    # Hace falta uno para webrtcvad: ver build/hooks/hook-webrtcvad.py.
    hookspath=[str(PROJECT / "build" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe_kwargs = dict(
    name="camvoice",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX rompe algunas DLL de Qt y dispara antivirus
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(PROJECT / "build" / "version_info.txt")
    if (PROJECT / "build" / "version_info.txt").exists()
    else None,
)

cli_kwargs = dict(exe_kwargs, name="camvoice-cli", console=True)

if ONE_FILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_kwargs)
    exe_cli = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **cli_kwargs)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_kwargs)
    exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, **cli_kwargs)
    coll = COLLECT(
        exe, exe_cli, a.binaries, a.datas,
        strip=False, upx=False, name="camvoice",
    )

    # Lo que va junto a los ejecutables y no dentro de _internal: los scripts
    # de instalación (buscan camvoice-cli.exe en la carpeta de arriba) y la
    # documentación mínima con las licencias. Se copia aquí para que el
    # paquete salga completo con un simple `pyinstaller build/camvoice.spec`.
    import shutil

    _OUT = Path(DISTPATH) / "camvoice"
    shutil.copytree(PROJECT / "instalacion", _OUT / "instalacion", dirs_exist_ok=True)
    for _doc in [
        "README.md",
        "docs/LICENCIAS.md",
        "docs/INSTALACION.md",
        "docs/REQUISITOS.md",
        "docs/VALIDACION_PC_NUEVA.md",
        "vendor/filters/ATRIBUCION.md",
    ]:
        _src = PROJECT / _doc
        if _src.exists():
            (_OUT / "docs").mkdir(exist_ok=True)
            shutil.copy2(_src, _OUT / "docs" / _src.name)
