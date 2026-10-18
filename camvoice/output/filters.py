"""Descubrimiento y registro de cámaras virtuales de Windows.

## Por qué este módulo existe

En Windows, una "cámara" que otras aplicaciones puedan seleccionar **no es un
proceso**: es un objeto COM que la aplicación receptora carga **dentro de su
propio proceso**. Teams no habla con nuestro programa; carga una DLL registrada
en el sistema y le pide fotogramas.

Eso impone un límite que no se puede sortear: **ninguna aplicación en Python
puede ser por sí misma una cámara de Windows.** Hace falta una DLL nativa
registrada. Lo que sí puede hacer un programa en Python es *alimentar* esa DLL
por memoria compartida, que es lo que hace `pyvirtualcam`.

| Opción | Quién registra | Nombre visible | Dependencia |
|---|---|---|---|
| Filtro de OBS | El instalador de OBS Studio | «OBS Virtual Camera» | Hay que instalar OBS |
| Filtro UnityCapture | Esta aplicación, **solo para el usuario** | el que queramos | Ninguna |

## Registro por usuario, sin administrador

COM y el enumerador de dispositivos de DirectShow leen `HKEY_CLASSES_ROOT`, que
es la **unión** de `HKCU\\Software\\Classes` y `HKLM\\Software\\Classes`. Si las
claves del filtro se escriben en la parte de `HKCU`, la cámara existe solo para
el usuario que la instaló y no hace falta UAC.

Lo que **no** sirve es `regsvr32`: `DllRegisterServer` de UnityCapture escribe
en `HKCR`, y una escritura en `HKCR` va a `HKLM` salvo que la clave ya exista en
`HKCU`. Por eso aquí se escriben las claves explícitamente. Su contenido no es
una suposición: se capturó ejecutando el propio `DllRegisterServer` de cada DLL
con `HKCR`, `HKLM` y `HKCU` redirigidas a claves temporales
(`RegOverridePredefKey`) en un proceso de su arquitectura. Ver
`docs/VALIDACION_INSTALACION_USUARIO.md` §2.

## Detalle que importa: 32 frente a 64 bits

Un filtro DirectShow solo se carga en procesos de su misma arquitectura. Las dos
DLL **no comparten CLSID** (la de 64 bits usa `...0010`, la de 32 bits
`...0020`), y `HKCU\\Software\\Classes\\CLSID` está redirigida por WOW64: la
variante de 32 bits se escribe con `KEY_WOW64_32KEY`, nunca componiendo a mano
`WOW6432Node`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "VIDEO_INPUT_CATEGORY",
    "DEFAULT_CAMERA_NAME",
    "RegisteredCamera",
    "FilterSpec",
    "UNITYCAPTURE_FILTERS",
    "UNITYCAPTURE_FILTER_DATA",
    "InstallReport",
    "ArchStatus",
    "list_directshow_cameras",
    "detect_backends",
    "describe_availability",
    "bundled_filters",
    "filters_dir",
    "user_filters_dir",
    "install_for_user",
    "uninstall_for_user",
    "user_install_status",
    "FilterError",
    "FilterConflict",
]

VIDEO_INPUT_CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
"""CLSID_VideoInputDeviceCategory: donde Windows lista las fuentes de video."""

_OBS_CLSID = "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"

# Pistas para deducir qué backend de pyvirtualcam alimenta cada filtro, por
# nombre amistoso en minúsculas. El CLSID de OBS es estable y se comprueba
# aparte; los de UnityCapture también se reconocen por CLSID más abajo.
_BACKEND_HINTS = (
    ("obs virtual camera", "obs"),
    ("unity video capture", "unitycapture"),
    ("camvoice", "unitycapture"),
)

DEFAULT_CAMERA_NAME = "CamVoice Virtual Camera"
"""Nombre con el que aparece la cámara en Teams, Zoom, Meet y el navegador.

Es el valor por defecto de la clave CLSID del filtro y el `FriendlyName` de su
instancia. `pyvirtualcam` localiza el dispositivo leyendo ese mismo valor."""


class FilterError(RuntimeError):
    """Fallo al registrar o quitar un filtro, con mensaje para el usuario."""


class FilterConflict(FilterError):
    """Ya hay un registro del sistema o de otro programa; no se toca nada."""


# --------------------------------------------------------------------------- #
# Datos verificados del filtro UnityCapture
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """Lo que `DllRegisterServer` de una DLL concreta escribe en el registro."""

    architecture: str
    dll_name: str
    sha256: str
    machine: int
    """Campo Machine de la cabecera PE: 0x8664 = x64, 0x14C = x86."""
    filter_clsid: str
    config_clsid: str
    """Página de propiedades del filtro («Unity Video Capture Configuration»)."""
    config_name: str = "Unity Video Capture Configuration"


UNITYCAPTURE_FILTERS: dict[str, FilterSpec] = {
    "64-bit": FilterSpec(
        architecture="64-bit",
        dll_name="UnityCaptureFilter64.dll",
        sha256="72812f5363d8ecb45632253f8c8c888844b1b62e27616f3c8cc21064ccde25e5",
        machine=0x8664,
        filter_clsid="{5C2CD55C-92AD-4999-8666-912BD3E70010}",
        config_clsid="{5C2CD55C-92AD-4999-8666-912BD3E70011}",
    ),
    "32-bit": FilterSpec(
        architecture="32-bit",
        dll_name="UnityCaptureFilter32.dll",
        sha256="aa3ebdf03dea7f3aab3dd7b724751f49ed71672256b57c6a19aa6809cabf30ba",
        machine=0x014C,
        filter_clsid="{5C2CD55C-92AD-4999-8666-912BD3E70020}",
        config_clsid="{5C2CD55C-92AD-4999-8666-912BD3E70021}",
    ),
}
"""Las dos DLL de `vendor/filters/`, identificadas por su hash.

Solo se registran binarios conocidos: los CLSID y `FilterData` se verificaron
para estos, y registrar a mano otra DLL con estos datos produciría una cámara
rota. Para cambiar de filtro hay que repetir la captura del §2 de
`docs/VALIDACION_INSTALACION_USUARIO.md` y añadir aquí su `FilterSpec`."""

UNITYCAPTURE_FILTER_DATA = bytes.fromhex(
    "02000000" "00002000" "01000000" "00000000"  # REGFILTER2 v2, MERIT_DO_NOT_USE, 1 pin
    "30706933" "08000000" "00000000" "01000000"  # "0pi3", REG_PINFLAG_B_OUTPUT, 1 tipo
    "00000000" "00000000"                        # sin medios, sin categoría de pin
    "30747933" "00000000" "38000000" "48000000"  # "0ty3", mayor en 0x38, menor en 0x48
    "76696473" "00001000" "800000aa" "00389b71"  # MEDIATYPE_Video
    "00000000" "00000000" "00000000" "00000000"  # GUID_NULL (cualquier subtipo)
)
"""`FilterData` de la instancia, idéntico en las dos DLL.

Es el `REGFILTER2` que `IFilterMapper2::RegisterFilter` serializa al ejecutar
`DllRegisterServer`; copiado byte a byte de esa captura, no construido."""

_INSTANCE_ROOT = rf"Software\Classes\CLSID\{VIDEO_INPUT_CATEGORY}\Instance"
_CLSID_ROOT = r"Software\Classes\CLSID"

REG_SZ = 1
REG_BINARY = 3


# --------------------------------------------------------------------------- #
# Acceso al registro
# --------------------------------------------------------------------------- #


class _Registry:
    """Acceso mínimo a `winreg`, sustituible en las pruebas.

    Las rutas son relativas a la raíz de la colmena (`Software\\Classes\\...`),
    `hive` es "HKCU" o "HKLM" y `view` "64-bit" o "32-bit". **Las escrituras
    solo aceptan HKCU**: es una barrera explícita, no una convención.
    """

    def _base(self, hive: str):  # noqa: ANN202
        import winreg

        return {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]

    @staticmethod
    def _flag(view: str) -> int:
        import winreg

        return winreg.KEY_WOW64_32KEY if view == "32-bit" else winreg.KEY_WOW64_64KEY

    @staticmethod
    def _guard(hive: str) -> None:
        if hive != "HKCU":
            raise FilterError(f"Intento de escritura fuera de HKCU ({hive}); bloqueado.")

    def read_values(self, hive: str, view: str, path: str) -> dict[str, tuple[object, int]] | None:
        """Valores de una clave, o None si la clave no existe."""
        import winreg

        try:
            key = winreg.OpenKey(self._base(hive), path, 0, winreg.KEY_READ | self._flag(view))
        except OSError:
            return None
        values: dict[str, tuple[object, int]] = {}
        with key:
            index = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = (data, kind)
                index += 1
        return values

    def subkeys(self, hive: str, view: str, path: str) -> list[str]:
        import winreg

        try:
            key = winreg.OpenKey(self._base(hive), path, 0, winreg.KEY_READ | self._flag(view))
        except OSError:
            return []
        names: list[str] = []
        with key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
        return names

    def write_values(self, hive: str, view: str, path: str, values: dict[str, tuple[object, int]]) -> None:
        self._guard(hive)
        import winreg

        with winreg.CreateKeyEx(
            self._base(hive), path, 0, winreg.KEY_WRITE | self._flag(view)
        ) as key:
            for name, (data, kind) in values.items():
                winreg.SetValueEx(key, name, 0, kind, data)

    def delete_tree(self, hive: str, view: str, path: str) -> None:
        """Borra la clave y sus subclaves. No falla si ya no existe."""
        self._guard(hive)
        import winreg

        for child in self.subkeys(hive, view, path):
            self.delete_tree(hive, view, rf"{path}\{child}")
        try:
            winreg.DeleteKeyEx(self._base(hive), path, self._flag(view), 0)
        except FileNotFoundError:
            pass


_registry: _Registry = _Registry()
"""Instancia en uso. Las pruebas la sustituyen por un registro en memoria."""


# --------------------------------------------------------------------------- #
# Enumeración
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RegisteredCamera:
    friendly_name: str
    clsid: str
    dll_path: str
    architecture: str
    """'64-bit' o '32-bit', según la vista del registro donde apareció."""
    scope: str = "sistema"
    """'usuario' (HKCU) o 'sistema' (HKLM)."""

    @property
    def dll_exists(self) -> bool:
        return bool(self.dll_path) and Path(self.dll_path).exists()

    @property
    def backend(self) -> str:
        """Backend de pyvirtualcam que puede alimentar este filtro, o ''."""
        clsid = self.clsid.upper()
        if clsid == _OBS_CLSID:
            return "obs"
        if any(clsid == spec.filter_clsid for spec in UNITYCAPTURE_FILTERS.values()):
            return "unitycapture"
        low = self.friendly_name.lower()
        for hint, backend in _BACKEND_HINTS:
            if hint in low:
                return backend
        return ""

    @property
    def label(self) -> str:
        state = "" if self.dll_exists else "  [DLL AUSENTE: registro huérfano]"
        backend = f"  backend={self.backend}" if self.backend else "  backend=desconocido"
        return f"{self.friendly_name} ({self.architecture}, {self.scope}){backend}{state}"


_VIEWS = ("64-bit", "32-bit")
_SCOPES = (("HKCU", "usuario"), ("HKLM", "sistema"))


def list_directshow_cameras() -> list[RegisteredCamera]:
    """Filtros registrados en la categoría de captura de video, en las cuatro
    combinaciones de colmena (usuario/sistema) y vista (64/32 bits).

    Es una lectura del registro, no una enumeración de DirectShow: dice qué hay
    escrito, no qué ve una aplicación. Para lo segundo está `output.dshow`.
    """
    if sys.platform != "win32":
        return []
    found: list[RegisteredCamera] = []
    for hive, scope in _SCOPES:
        for view in _VIEWS:
            for name in _registry.subkeys(hive, view, _INSTANCE_ROOT):
                camera = _read_instance(hive, scope, view, name)
                if camera is not None:
                    found.append(camera)
    return found


def _read_instance(hive: str, scope: str, view: str, name: str) -> RegisteredCamera | None:
    values = _registry.read_values(hive, view, rf"{_INSTANCE_ROOT}\{name}")
    if values is None:
        return None
    friendly = str(values.get("FriendlyName", (name,))[0])
    clsid = str(values.get("CLSID", (name,))[0]).upper()
    # Como hace COM: el servidor se busca primero en el usuario y luego en el
    # sistema. Así un filtro registrado en HKCU muestra la DLL que se cargará.
    dll = ""
    for server_hive in ("HKCU", "HKLM") if hive == "HKCU" else ("HKLM",):
        server = _registry.read_values(server_hive, view, rf"{_CLSID_ROOT}\{clsid}\InprocServer32")
        if server and server.get("", ("",))[0]:
            dll = str(server[""][0])
            break
    return RegisteredCamera(friendly, clsid, dll, view, scope)


# --------------------------------------------------------------------------- #
# Selección de backend
# --------------------------------------------------------------------------- #


def detect_backends(*, prefer_independent: bool = True) -> list[str]:
    """Backends de pyvirtualcam utilizables, el preferido primero.

    Con `prefer_independent` se coloca delante el filtro propio, de modo que la
    aplicación no quede supeditada a que OBS esté presente ni compita con él.
    """
    available: list[str] = []
    for camera in list_directshow_cameras():
        backend = camera.backend
        if backend and camera.dll_exists and backend not in available:
            available.append(backend)
    if prefer_independent:
        available.sort(key=lambda b: 1 if b == "obs" else 0)
    return available


def describe_availability() -> str:
    """Resumen legible de la situación de la cámara virtual en este equipo."""
    lines = ["Cámara propia de camvoice:"]
    for arch, status in user_install_status().items():
        lines.append(f"  - {arch}: {status.label}")
    cameras = list_directshow_cameras()
    virtual = [c for c in cameras if c.backend]
    if not virtual:
        lines.append(
            "No hay ninguna cámara virtual registrada. Ejecuta "
            "«camvoice-cli install-camera» para registrarla solo para tu "
            "usuario, sin permisos de administrador."
        )
        return "\n".join(lines)
    lines.append("Cámaras virtuales registradas (usuario = solo tú, sistema = todo el equipo):")
    lines += [f"  - {c.label}" for c in virtual]

    arches = {c.architecture for c in virtual}
    if "32-bit" not in arches:
        lines.append(
            "  Aviso: solo hay filtro de 64 bits. Una aplicación de "
            "videollamada de 32 bits no verá la cámara."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Archivos
# --------------------------------------------------------------------------- #


def filters_dir() -> Path:
    """Dónde vienen las DLL de filtro que acompañan a la aplicación.

    Funciona tanto ejecutando desde el código fuente como desde el ejecutable
    de PyInstaller, que extrae los datos a `sys._MEIPASS`. Es el **origen** de
    la copia: la DLL registrada nunca apunta aquí (ver `user_filters_dir`).
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "filters"
    return Path(__file__).resolve().parent.parent.parent / "vendor" / "filters"


def user_filters_dir() -> Path:
    """Copia estable de las DLL, dentro del perfil del usuario.

    `%LOCALAPPDATA%` y no `%APPDATA%`: son binarios de esta máquina, no
    configuración que deba seguir a un perfil itinerante. `CAMVOICE_FILTERS_HOME`
    permite redirigirla (pruebas, rutas con espacios o acentos).
    """
    override = os.environ.get("CAMVOICE_FILTERS_HOME")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "camvoice" / "filters"
    return Path.home() / "AppData" / "Local" / "camvoice" / "filters"


def bundled_filters() -> dict[str, Path]:
    """DLL de filtro disponibles para registrar, por arquitectura."""
    directory = filters_dir()
    out: dict[str, Path] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.dll")):
        name = path.name.lower()
        if "32" in name and "64" not in name:
            out.setdefault("32-bit", path)
        else:
            out.setdefault("64-bit", path)
    return out


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_machine(path: Path) -> int | None:
    """Campo Machine de la cabecera PE, o None si no es un binario PE."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    if head[:2] != b"MZ" or len(head) < 0x40:
        return None
    offset = int.from_bytes(head[0x3C:0x40], "little")
    if offset + 6 > len(head) or head[offset:offset + 4] != b"PE\0\0":
        return None
    return int.from_bytes(head[offset + 4:offset + 6], "little")


def _validate_source(path: Path) -> FilterSpec:
    """Comprueba que la DLL es una de las verificadas y devuelve su ficha."""
    if not path.is_file():
        raise FilterError(f"No existe la DLL del filtro: {path}")
    digest = sha256_of(path)
    for spec in UNITYCAPTURE_FILTERS.values():
        if digest == spec.sha256:
            machine = pe_machine(path)
            if machine != spec.machine:
                raise FilterError(
                    f"«{path.name}» tiene el hash de la DLL de {spec.architecture} pero "
                    f"su cabecera PE no corresponde (Machine={machine!r})."
                )
            return spec
    raise FilterError(
        f"«{path.name}» no es una de las DLL verificadas (SHA-256 {digest[:16]}...).\n"
        "El registro por usuario escribe a mano los CLSID y FilterData de las DLL "
        "de UnityCapture incluidas; con otra DLL la cámara quedaría rota. Ver "
        "docs/VALIDACION_INSTALACION_USUARIO.md §2 para validar un filtro nuevo."
    )


def _install_file(source: Path, spec: FilterSpec) -> Path:
    """Copia la DLL a una carpeta propia de su contenido y la verifica.

    La carpeta lleva el hash en el nombre (`64-bit-72812f5363d8`). Así una
    actualización nunca sobrescribe una DLL que Teams pueda tener cargada: la
    nueva va a otra carpeta y el registro se cambia después de copiarla.
    """
    target_dir = user_filters_dir() / f"{spec.architecture}-{spec.sha256[:12]}"
    target = target_dir / spec.dll_name
    if target.is_file() and sha256_of(target) == spec.sha256:
        return target.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".tmp")
    try:
        shutil.copyfile(source, partial)
        if sha256_of(partial) != spec.sha256:
            raise FilterError(f"La copia de {spec.dll_name} quedó corrupta en {partial}.")
        os.replace(partial, target)
    except OSError as exc:
        raise FilterError(
            f"No se pudo copiar {spec.dll_name} a {target_dir}: {exc}\n"
            "La carpeta está dentro de tu perfil; si una directiva de la "
            "organización la bloquea, la instalación por usuario no es posible "
            "en este equipo."
        ) from exc
    finally:
        partial.unlink(missing_ok=True)
    return target.resolve()


def _is_ours(dll_path: str) -> bool:
    """¿Apunta esta ruta a una DLL copiada por nosotros en el perfil?"""
    if not dll_path:
        return False
    try:
        candidate = Path(dll_path).resolve()
        root = user_filters_dir().resolve()
    except OSError:
        return False
    return str(candidate).casefold().startswith(str(root).casefold() + os.sep)


def _remove_stale_files(keep: set[Path]) -> list[str]:
    """Borra copias antiguas. Las que estén en uso se quedan y se informa."""
    root = user_filters_dir()
    locked: list[str] = []
    if not root.is_dir():
        return locked
    keep_dirs = {p.parent.resolve() for p in keep}
    for directory in root.iterdir():
        if not directory.is_dir() or directory.resolve() in keep_dirs:
            continue
        try:
            shutil.rmtree(directory)
        except OSError:
            locked.append(str(directory))
    if not keep and not locked:
        try:
            root.rmdir()
            root.parent.rmdir()  # %LOCALAPPDATA%\camvoice, solo si quedó vacía
        except OSError:
            pass
    return locked


# --------------------------------------------------------------------------- #
# Registro por usuario
# --------------------------------------------------------------------------- #


def _keys_for(spec: FilterSpec, dll: str, camera_name: str) -> list[tuple[str, dict[str, tuple[object, int]]]]:
    """Claves y valores exactos que `DllRegisterServer` escribe para esta DLL."""
    server = {"": (dll, REG_SZ), "ThreadingModel": ("Both", REG_SZ)}
    return [
        (rf"{_CLSID_ROOT}\{spec.filter_clsid}", {"": (camera_name, REG_SZ)}),
        (rf"{_CLSID_ROOT}\{spec.filter_clsid}\InprocServer32", server),
        (rf"{_CLSID_ROOT}\{spec.config_clsid}", {"": (spec.config_name, REG_SZ)}),
        (rf"{_CLSID_ROOT}\{spec.config_clsid}\InprocServer32", server),
        (
            rf"{_INSTANCE_ROOT}\{spec.filter_clsid}",
            {
                "FriendlyName": (camera_name, REG_SZ),
                "CLSID": (spec.filter_clsid, REG_SZ),
                "FilterData": (UNITYCAPTURE_FILTER_DATA, REG_BINARY),
                # DllRegisterServer lo añade abriendo la instancia en HKLM sin
                # comprobar el resultado (RegisterFilters en UnityCaptureFilter.cpp);
                # por eso no aparece en la captura. Algunas aplicaciones exigen
                # DevicePath para listar una cámara.
                "DevicePath": ("foo:bar", REG_SZ),
            },
        ),
    ]


def _own_key_paths(spec: FilterSpec) -> list[str]:
    """Raíces de lo que escribimos; borrarlas quita todo lo nuestro."""
    return [
        rf"{_CLSID_ROOT}\{spec.filter_clsid}",
        rf"{_CLSID_ROOT}\{spec.config_clsid}",
        rf"{_INSTANCE_ROOT}\{spec.filter_clsid}",
    ]


@dataclass(slots=True)
class ArchStatus:
    """Estado del registro por usuario de una arquitectura."""

    architecture: str
    state: str
    """'instalado', 'no instalado', 'ajeno', 'sistema', 'roto' o 'no verificable'."""
    dll_path: str = ""
    name: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {
            "instalado": "Instalado para este usuario",
            "no instalado": "No instalado",
            "sistema": "Registro existente del sistema (solo lectura)",
            "ajeno": "Registro por usuario de otro programa (no se toca)",
            "roto": "Instalado para este usuario, pero dañado",
            "no verificable": "No verificable",
        }[self.state]


def _owner_of_user_key(spec: FilterSpec) -> str:
    """'' si no hay clave en HKCU, 'ours' si es nuestra, 'foreign' si no."""
    present = [
        _registry.read_values("HKCU", spec.architecture, path) is not None
        for path in _own_key_paths(spec)
    ]
    if not any(present):
        return ""
    server = _registry.read_values(
        "HKCU", spec.architecture, rf"{_CLSID_ROOT}\{spec.filter_clsid}\InprocServer32"
    )
    dll = str((server or {}).get("", ("",))[0])
    return "ours" if _is_ours(dll) else "foreign"


def _system_has(spec: FilterSpec) -> bool:
    """¿Hay un registro del mismo filtro en HKLM? Solo lectura."""
    return any(
        _registry.read_values("HKLM", spec.architecture, path) is not None
        for path in _own_key_paths(spec)
    )


def _arch_status(spec: FilterSpec) -> ArchStatus:
    try:
        owner = _owner_of_user_key(spec)
        system = _system_has(spec)
    except OSError as exc:
        return ArchStatus(spec.architecture, "no verificable", problems=[str(exc)])
    if owner == "foreign":
        return ArchStatus(spec.architecture, "ajeno")
    if owner == "":
        return ArchStatus(spec.architecture, "sistema" if system else "no instalado")

    server = _registry.read_values(
        "HKCU", spec.architecture, rf"{_CLSID_ROOT}\{spec.filter_clsid}\InprocServer32"
    ) or {}
    dll = str(server.get("", ("",))[0])
    name = str((_registry.read_values(
        "HKCU", spec.architecture, rf"{_CLSID_ROOT}\{spec.filter_clsid}"
    ) or {}).get("", ("",))[0])
    status = ArchStatus(spec.architecture, "instalado", dll_path=dll, name=name)

    for path, expected in _keys_for(spec, dll, name):
        actual = _registry.read_values("HKCU", spec.architecture, path)
        if actual is None:
            status.problems.append(f"falta la clave {path}")
            continue
        for value_name, (data, kind) in expected.items():
            if actual.get(value_name) != (data, kind):
                status.problems.append(f"valor incorrecto {path}\\{value_name or '(predeterminado)'}")
    if not Path(dll).is_file():
        status.problems.append(f"la DLL registrada no existe: {dll}")
    elif sha256_of(Path(dll)) != spec.sha256:
        status.problems.append(f"la DLL registrada fue modificada: {dll}")
    if system:
        status.problems.append(
            "también hay un registro del sistema (HKLM) del mismo filtro; para este "
            "usuario prevalece el suyo"
        )
    if any(not p.startswith("también") for p in status.problems):
        status.state = "roto"
    return status


def user_install_status() -> dict[str, ArchStatus]:
    """Estado por arquitectura, sin modificar nada."""
    if sys.platform != "win32":
        return {
            arch: ArchStatus(arch, "no verificable", problems=["solo existe en Windows"])
            for arch in UNITYCAPTURE_FILTERS
        }
    return {arch: _arch_status(spec) for arch, spec in UNITYCAPTURE_FILTERS.items()}


@dataclass(slots=True)
class InstallReport:
    installed: dict[str, str] = field(default_factory=dict)
    """Arquitectura -> ruta de la DLL registrada."""
    removed: list[str] = field(default_factory=list)
    locked_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{arch}: {dll}" for arch, dll in self.installed.items()]
        lines += self.removed + self.notes
        return "\n".join(lines)

    def locked_summary(self) -> str:
        """Aviso sobre copias que no se pudieron borrar porque están en uso."""
        if not self.locked_files:
            return ""
        return (
            "Copias en uso por otra aplicación (navegador, Teams...); no se borran ahora. "
            "Ya no están registradas; se quitarán en la próxima instalación o "
            "desinstalación con esa aplicación cerrada: "
            + ", ".join(self.locked_files)
        )


def _snapshot(spec: FilterSpec) -> dict[str, dict[str, tuple[object, int]] | None]:
    """Estado previo de cada clave que se va a escribir, para el rollback."""
    paths = [path for path, _ in _keys_for(spec, "", "")]
    return {path: _registry.read_values("HKCU", spec.architecture, path) for path in paths}


def _restore(spec: FilterSpec, snapshot: dict[str, dict[str, tuple[object, int]] | None]) -> list[str]:
    """Deja las claves como estaban. Solo toca claves que eran nuestras o no existían.

    Sigue aunque falle una clave, para deshacer todo lo posible, y devuelve lo
    que no se pudo restaurar.
    """
    failed: list[str] = []
    for path in sorted(snapshot, key=len, reverse=True):
        if snapshot[path] is None:
            try:
                _registry.delete_tree("HKCU", spec.architecture, path)
            except OSError as exc:
                failed.append(f"{path}: {exc}")
    for path in sorted(snapshot, key=len):
        before = snapshot[path]
        if before is not None:
            try:
                _registry.write_values("HKCU", spec.architecture, path, before)
            except OSError as exc:
                failed.append(f"{path}: {exc}")
    try:
        _prune_empty_parents(spec.architecture)
    except OSError as exc:
        failed.append(str(exc))
    return failed


def _prune_empty_parents(view: str) -> None:
    """Quita `{categoría}\\Instance` y `{categoría}` de HKCU si quedaron vacías.

    Son contenedores que creamos al escribir la instancia; vacíos no aportan
    nada y no pueden contener datos de nadie.
    """
    for path in (_INSTANCE_ROOT, rf"{_CLSID_ROOT}\{VIDEO_INPUT_CATEGORY}"):
        values = _registry.read_values("HKCU", view, path)
        if values == {} and not _registry.subkeys("HKCU", view, path):
            _registry.delete_tree("HKCU", view, path)


def install_for_user(
    dll: Path | None = None,
    *,
    camera_name: str = DEFAULT_CAMERA_NAME,
) -> InstallReport:
    """Registra la cámara **solo para el usuario actual**, sin elevación.

    1. Valida cada DLL contra los hashes verificados.
    2. Comprueba conflictos: un registro del sistema (HKLM) o uno por usuario de
       otro programa se respetan y la instalación se detiene sin escribir nada.
    3. Copia las DLL a `user_filters_dir()` y verifica la copia.
    4. Escribe las claves en HKCU; si algo falla, deshace exactamente lo de esta
       ejecución.
    5. Relee el registro y comprueba que coincide.

    Es idempotente: repetirla deja el mismo estado.
    """
    if sys.platform != "win32":
        raise FilterError("El registro de filtros DirectShow solo existe en Windows.")
    camera_name = camera_name.strip()
    if not camera_name:
        raise FilterError("El nombre de la cámara no puede estar vacío.")

    sources = [Path(dll)] if dll is not None else list(bundled_filters().values())
    if not sources:
        raise FilterError(
            "No se encontró ninguna DLL de filtro para registrar.\n"
            f"Se buscaba en: {filters_dir()}\n\n"
            "Esta copia de la aplicación se distribuyó sin el filtro de cámara "
            "virtual. Ver docs/CAMARA-VIRTUAL.md."
        )
    plan = [(source, _validate_source(source)) for source in sources]

    conflicts: list[str] = []
    for _, spec in plan:
        owner = _owner_of_user_key(spec)
        if owner == "foreign":
            conflicts.append(
                f"{spec.architecture}: ya hay un registro por usuario de este filtro "
                "que apunta a una DLL que no es de camvoice. No se modifica."
            )
        if _system_has(spec):
            conflicts.append(
                f"{spec.architecture}: el filtro ya está registrado para todo el equipo "
                "(HKLM). No se modifica ni se oculta con un registro por usuario."
            )
    if conflicts:
        raise FilterConflict(
            "Conflicto con un registro existente; no se ha cambiado nada.\n  "
            + "\n  ".join(conflicts)
            + "\nVer «camvoice-cli verify-camera» y docs/VALIDACION_INSTALACION_USUARIO.md."
        )

    report = InstallReport()
    done: list[tuple[FilterSpec, dict]] = []
    try:
        for source, spec in plan:
            installed = _install_file(source, spec)
            snapshot = _snapshot(spec)
            done.append((spec, snapshot))
            for path, values in _keys_for(spec, str(installed), camera_name):
                _registry.write_values("HKCU", spec.architecture, path, values)
            report.installed[spec.architecture] = str(installed)
    except Exception as exc:
        leftovers = [item for spec, snapshot in reversed(done) for item in _restore(spec, snapshot)]
        if leftovers:
            raise FilterError(
                f"Falló el registro por usuario ({exc}) y no se pudo deshacer del todo. "
                "Ejecuta «camvoice-cli install-camera --uninstall». Claves pendientes en "
                "HKCU:\n  " + "\n  ".join(leftovers)
            ) from exc
        if isinstance(exc, FilterError):
            raise
        raise FilterError(f"Falló el registro por usuario y se ha deshecho: {exc}") from exc

    for spec, _ in done:
        status = _arch_status(spec)
        broken = [p for p in status.problems if not p.startswith("también")]
        if status.state != "instalado" or broken:
            raise FilterError(
                f"El registro de {spec.architecture} no quedó como se esperaba: "
                + "; ".join(broken or [status.label])
            )

    keep = {Path(p) for p in report.installed.values()}
    # Las otras arquitecturas ya instaladas también se conservan.
    for arch, status in user_install_status().items():
        if status.state in ("instalado", "roto") and status.dll_path:
            keep.add(Path(status.dll_path))
    report.locked_files = _remove_stale_files(keep)
    return report


def uninstall_for_user() -> InstallReport:
    """Quita el registro por usuario y las DLL copiadas. Idempotente.

    Solo borra claves cuyo servidor apunte a nuestra carpeta del perfil: un
    registro del sistema o de otro programa se deja intacto y se informa.
    """
    if sys.platform != "win32":
        raise FilterError("El registro de filtros DirectShow solo existe en Windows.")
    report = InstallReport()
    for spec in UNITYCAPTURE_FILTERS.values():
        owner = _owner_of_user_key(spec)
        if owner == "ours":
            # Primero el registro, después los archivos: así nunca queda una
            # entrada apuntando a una DLL que ya no existe.
            for path in _own_key_paths(spec):
                _registry.delete_tree("HKCU", spec.architecture, path)
            _prune_empty_parents(spec.architecture)
            report.removed.append(f"{spec.architecture}: registro por usuario quitado")
        elif owner == "foreign":
            report.notes.append(
                f"{spec.architecture}: hay un registro por usuario de otro programa; no se toca."
            )
        if _system_has(spec):
            report.notes.append(
                f"{spec.architecture}: hay un registro del sistema (HKLM); no se toca."
            )
    report.locked_files = _remove_stale_files(set())
    if not report.removed:
        report.notes.insert(0, "No había ningún registro por usuario de camvoice.")
    return report
