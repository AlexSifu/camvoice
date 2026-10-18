"""Pruebas del descubrimiento y del registro por usuario de la cámara virtual.

Casi todas usan un registro en memoria (`FakeRegistry`) que sustituye a
`winreg`: así se comprueba exactamente qué claves se escribirían, en qué
colmena y en qué vista, sin tocar la máquina. Las DLL son binarios PE mínimos
fabricados en la prueba, con sus propias fichas `FilterSpec`.

La prueba contra el registro y DirectShow reales está en
`test_camara_por_usuario_integracion.py` y solo corre si se pide.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import re
import sys
from pathlib import Path

import pytest

from camvoice.output import filters
from camvoice.output.filters import (
    REG_BINARY,
    REG_SZ,
    UNITYCAPTURE_FILTER_DATA,
    VIDEO_INPUT_CATEGORY,
    FilterConflict,
    FilterError,
    FilterSpec,
    RegisteredCamera,
    bundled_filters,
    describe_availability,
    detect_backends,
    filters_dir,
    install_for_user,
    list_directshow_cameras,
    uninstall_for_user,
    user_install_status,
)

ROOT = Path(__file__).resolve().parent.parent
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")


# --------------------------------------------------------------------------- #
# Registro en memoria
# --------------------------------------------------------------------------- #


class FakeRegistry:
    """Imita la parte de `_Registry` que usa el módulo.

    Guarda las claves por (colmena, vista, ruta en minúsculas), crea los
    padres como hace `CreateKeyEx` y registra cada operación para poder
    afirmar qué se tocó. Escribir fuera de HKCU lanza, igual que el real.
    """

    def __init__(self) -> None:
        self.keys: dict[tuple[str, str, str], dict[str, tuple[object, int]]] = {}
        self.names: dict[tuple[str, str, str], str] = {}
        self.log: list[tuple[str, str, str, str]] = []
        self.fail_on_write: int | None = None

    @staticmethod
    def _k(hive: str, view: str, path: str) -> tuple[str, str, str]:
        return hive, view, path.lower()

    def seed(self, hive: str, view: str, path: str, values: dict[str, tuple[object, int]]) -> None:
        """Estado previo; no cuenta como escritura del programa."""
        self._create(hive, view, path)
        self.keys[self._k(hive, view, path)].update(values)

    def _create(self, hive: str, view: str, path: str) -> None:
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            sub = "\\".join(parts[:i])
            key = self._k(hive, view, sub)
            self.keys.setdefault(key, {})
            self.names.setdefault(key, parts[i - 1])

    def read_values(self, hive, view, path):  # noqa: ANN001, ANN201
        values = self.keys.get(self._k(hive, view, path))
        return None if values is None else dict(values)

    def subkeys(self, hive, view, path):  # noqa: ANN001, ANN201
        prefix = path.lower() + "\\"
        out = []
        for h, v, p in self.keys:
            if h == hive and v == view and p.startswith(prefix) and "\\" not in p[len(prefix):]:
                out.append(self.names[(h, v, p)])
        return out

    def write_values(self, hive, view, path, values):  # noqa: ANN001, ANN201
        filters._Registry._guard(hive)
        if self.fail_on_write is not None:
            self.fail_on_write -= 1
            if self.fail_on_write < 0:
                self.fail_on_write = None  # un fallo puntual; el rollback sí puede escribir
                raise OSError("fallo simulado de RegSetValueEx")
        self.log.append(("write", hive, view, path))
        self._create(hive, view, path)
        self.keys[self._k(hive, view, path)].update(values)

    def delete_tree(self, hive, view, path):  # noqa: ANN001, ANN201
        filters._Registry._guard(hive)
        self.log.append(("delete", hive, view, path))
        prefix = path.lower()
        for key in [k for k in self.keys if k[0] == hive and k[1] == view
                    and (k[2] == prefix or k[2].startswith(prefix + "\\"))]:
            del self.keys[key]
            del self.names[key]

    def snapshot(self) -> dict:
        return {k: dict(v) for k, v in self.keys.items()}


def _fake_pe(machine: int, payload: bytes) -> bytes:
    head = bytearray(b"MZ" + b"\0" * 0x3A) + (0x40).to_bytes(4, "little")
    head += b"PE\0\0" + machine.to_bytes(2, "little")
    return bytes(head) + payload


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Registro falso, DLL falsas y carpeta de usuario con espacios y acentos."""
    source = tmp_path / "origen"
    source.mkdir()
    specs = {}
    for arch, machine, suffix in (("64-bit", 0x8664, "10"), ("32-bit", 0x014C, "20")):
        bits = arch[:2]
        name = f"UnityCaptureFilter{bits}.dll"
        data = _fake_pe(machine, f"filtro {arch}".encode())
        (source / name).write_bytes(data)
        specs[arch] = FilterSpec(
            architecture=arch,
            dll_name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            machine=machine,
            filter_clsid=f"{{5C2CD55C-92AD-4999-8666-912BD3E700{suffix}}}",
            config_clsid=f"{{5C2CD55C-92AD-4999-8666-912BD3E700{int(suffix) + 1}}}",
        )
    user_dir = tmp_path / "Perfil de José Ñúñez" / "camvoice" / "filters"
    registry = FakeRegistry()
    for view in ("64-bit", "32-bit"):
        registry.seed("HKCU", view, CLSID, {})  # existe en cualquier perfil
    monkeypatch.setattr(filters, "UNITYCAPTURE_FILTERS", specs)
    monkeypatch.setattr(filters, "_registry", registry)
    monkeypatch.setattr(filters, "filters_dir", lambda: source)
    monkeypatch.setenv("CAMVOICE_FILTERS_HOME", str(user_dir))
    monkeypatch.setattr(sys, "platform", "win32")

    class Env:
        pass

    e = Env()
    e.registry, e.specs, e.source, e.user_dir = registry, specs, source, user_dir
    return e


CLSID = r"Software\Classes\CLSID"
INSTANCE = rf"{CLSID}\{VIDEO_INPUT_CATEGORY}\Instance"


# --------------------------------------------------------------------------- #
# 1. Solo HKCU
# --------------------------------------------------------------------------- #


def test_la_instalacion_solo_escribe_en_hkcu(env):
    install_for_user()
    assert env.registry.log, "no se escribió nada"
    assert {hive for _, hive, _, _ in env.registry.log} == {"HKCU"}


def test_el_registro_real_rechaza_escribir_en_hklm():
    """La barrera está en el acceso al registro, no solo en quien lo llama."""
    reg = filters._Registry()
    with pytest.raises(FilterError, match="HKCU"):
        reg.write_values("HKLM", "64-bit", r"Software\Classes\CLSID\{X}", {"": ("x", REG_SZ)})
    with pytest.raises(FilterError, match="HKCU"):
        reg.delete_tree("HKLM", "64-bit", r"Software\Classes\CLSID\{X}")


def test_desinstalar_solo_borra_en_hkcu(env):
    install_for_user()
    env.registry.log.clear()
    uninstall_for_user()
    assert {hive for _, hive, _, _ in env.registry.log} == {"HKCU"}


# --------------------------------------------------------------------------- #
# 2. Sin regsvr32 ni elevación
# --------------------------------------------------------------------------- #

_FORBIDDEN_PY = re.compile(r"regsvr32\.exe|\brunas\b|ShellExecuteW", re.IGNORECASE)
_FORBIDDEN_SCRIPT = re.compile(r"regsvr32|-Verb\s+RunAs|\brunas\b|Request-Elevacion", re.IGNORECASE)


def test_el_codigo_no_invoca_regsvr32_ni_pide_elevacion():
    """Ningún literal de cadena del paquete lanza regsvr32 o el verbo runas."""
    offenders = []
    for path in (ROOT / "camvoice").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _FORBIDDEN_PY.search(node.value) and not _is_docstring(tree, node):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def _is_docstring(tree: ast.AST, node: ast.Constant) -> bool:
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and body[0].value is node:
            return True
    return False


def test_los_scripts_de_instalacion_no_elevan():
    scripts = list((ROOT / "instalacion").glob("*.ps1")) + list((ROOT / "instalacion").glob("*.bat"))
    assert scripts
    offenders = [p.name for p in scripts if _FORBIDDEN_SCRIPT.search(p.read_text(encoding="utf-8-sig"))]
    assert not offenders, offenders


def test_los_scripts_no_escriben_en_hklm():
    for path in (ROOT / "instalacion").glob("*.ps1"):
        text = path.read_text(encoding="utf-8-sig")
        assert not re.search(r"(New-Item|Set-ItemProperty|Remove-Item|New-ItemProperty)[^\n]*HKLM:", text), path.name
        assert not re.search(r"\\WOW6432Node", text, re.IGNORECASE), f"{path.name} compone WOW6432Node a mano"


# --------------------------------------------------------------------------- #
# 3-5. Claves, tipos, datos, vistas y FilterData
# --------------------------------------------------------------------------- #


def _expected(spec: FilterSpec, dll: str, name: str) -> dict[str, dict]:
    server = {"": (dll, REG_SZ), "ThreadingModel": ("Both", REG_SZ)}
    return {
        rf"{CLSID}\{spec.filter_clsid}": {"": (name, REG_SZ)},
        rf"{CLSID}\{spec.filter_clsid}\InprocServer32": server,
        rf"{CLSID}\{spec.config_clsid}": {"": ("Unity Video Capture Configuration", REG_SZ)},
        rf"{CLSID}\{spec.config_clsid}\InprocServer32": server,
        rf"{INSTANCE}\{spec.filter_clsid}": {
            "FriendlyName": (name, REG_SZ),
            "CLSID": (spec.filter_clsid, REG_SZ),
            "FilterData": (UNITYCAPTURE_FILTER_DATA, REG_BINARY),
            "DevicePath": ("foo:bar", REG_SZ),
        },
    }


def test_escribe_exactamente_las_claves_de_dllregisterserver(env):
    report = install_for_user(camera_name="Mi Cámara")
    for arch, spec in env.specs.items():
        dll = report.installed[arch]
        for path, values in _expected(spec, dll, "Mi Cámara").items():
            assert env.registry.read_values("HKCU", arch, path) == values, path


def test_cada_arquitectura_va_en_su_vista(env):
    install_for_user()
    views = {(view, path.split("\\")[3]) for _, _, view, path in env.registry.log}
    assert ("64-bit", env.specs["64-bit"].filter_clsid) in views
    assert ("32-bit", env.specs["32-bit"].filter_clsid) in views
    assert ("64-bit", env.specs["32-bit"].filter_clsid) not in views
    assert ("32-bit", env.specs["64-bit"].filter_clsid) not in views
    assert not any("wow6432node" in path.lower() for _, _, _, path in env.registry.log)


def test_filterdata_es_el_capturado_byte_a_byte():
    """REGFILTER2 v2, mérito 0x200000, un pin de salida, MEDIATYPE_Video."""
    data = UNITYCAPTURE_FILTER_DATA
    assert len(data) == 88
    assert int.from_bytes(data[0:4], "little") == 2
    assert int.from_bytes(data[4:8], "little") == 0x00200000
    assert int.from_bytes(data[8:12], "little") == 1
    assert data[16:20] == b"0pi3"
    assert data[0x28:0x2C] == b"0ty3"
    assert int.from_bytes(data[0x30:0x34], "little") == 0x38
    assert int.from_bytes(data[0x34:0x38], "little") == 0x48
    assert data[0x38:0x48] == bytes.fromhex("7669647300001000800000aa00389b71")
    assert data[0x48:0x58] == bytes(16)


def test_las_fichas_reales_coinciden_con_las_dll_incluidas():
    """Los hashes de UNITYCAPTURE_FILTERS son los de vendor/filters."""
    found = bundled_filters()
    if not found:
        pytest.skip("este árbol no incluye las DLL del filtro")
    for arch, path in found.items():
        spec = filters.UNITYCAPTURE_FILTERS[arch]
        assert filters.sha256_of(path) == spec.sha256
        assert filters.pe_machine(path) == spec.machine
    clsids = [s.filter_clsid for s in filters.UNITYCAPTURE_FILTERS.values()]
    assert len(set(clsids)) == 2, "las dos arquitecturas no comparten CLSID"


# --------------------------------------------------------------------------- #
# 6. Copia de la DLL y alteraciones
# --------------------------------------------------------------------------- #


def test_la_dll_se_copia_al_perfil_y_el_registro_apunta_a_la_copia(env):
    report = install_for_user()
    for arch, dll in report.installed.items():
        path = Path(dll)
        assert env.user_dir.resolve() in path.parents
        assert filters.sha256_of(path) == env.specs[arch].sha256
        assert path.parent.name == f"{arch}-{env.specs[arch].sha256[:12]}"


def test_borrar_el_origen_no_rompe_la_instalacion(env):
    """Mover o borrar la carpeta extraída no debe dejar la cámara huérfana."""
    install_for_user()
    for dll in env.source.iterdir():
        dll.unlink()
    assert all(s.state == "instalado" for s in user_install_status().values())


def test_una_dll_alterada_se_detecta(env):
    report = install_for_user()
    Path(report.installed["64-bit"]).write_bytes(b"MZ manipulada")
    status = user_install_status()["64-bit"]
    assert status.state == "roto"
    assert any("modificada" in p for p in status.problems)


def test_una_dll_borrada_se_detecta_y_reinstalar_la_repara(env):
    report = install_for_user()
    Path(report.installed["32-bit"]).unlink()
    assert user_install_status()["32-bit"].state == "roto"
    install_for_user()
    assert user_install_status()["32-bit"].state == "instalado"


def test_una_dll_desconocida_se_rechaza_sin_escribir(env, tmp_path):
    other = tmp_path / "Otra64.dll"
    other.write_bytes(_fake_pe(0x8664, b"otra"))
    with pytest.raises(FilterError, match="verificadas"):
        install_for_user(other)
    assert env.registry.log == []


def test_una_cabecera_pe_incoherente_se_rechaza(env, monkeypatch):
    spec = env.specs["64-bit"]
    monkeypatch.setitem(env.specs, "64-bit", dataclasses.replace(spec, machine=0x014C))
    with pytest.raises(FilterError, match="cabecera PE"):
        install_for_user(env.source / spec.dll_name)
    assert env.registry.log == []


def test_sin_dll_el_mensaje_es_accionable(env, tmp_path, monkeypatch):
    """CA-12: el error dice dónde se buscó y qué hacer, no solo que falló."""
    empty = tmp_path / "vacio"
    empty.mkdir()
    monkeypatch.setattr(filters, "filters_dir", lambda: empty)
    with pytest.raises(FilterError) as info:
        install_for_user()
    assert str(empty) in str(info.value)
    assert "CAMARA-VIRTUAL" in str(info.value)


def test_una_dll_inexistente_falla_limpio(env, tmp_path):
    with pytest.raises(FilterError):
        install_for_user(tmp_path / "no-existe.dll")


# --------------------------------------------------------------------------- #
# 7. Idempotencia
# --------------------------------------------------------------------------- #


def test_instalar_dos_veces_deja_el_mismo_estado(env):
    install_for_user()
    first = env.registry.snapshot()
    files = sorted(p.relative_to(env.user_dir) for p in env.user_dir.rglob("*"))
    install_for_user()
    assert env.registry.snapshot() == first
    assert sorted(p.relative_to(env.user_dir) for p in env.user_dir.rglob("*")) == files


def test_reinstalar_con_otro_nombre_lo_actualiza(env):
    install_for_user(camera_name="Uno")
    install_for_user(camera_name="Dos")
    names = {c.friendly_name for c in list_directshow_cameras()}
    assert names == {"Dos"}


# --------------------------------------------------------------------------- #
# 8. Rollback
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fail_after", [0, 1, 4, 7])
def test_un_fallo_a_mitad_deshace_lo_escrito(env, fail_after):
    foreign = rf"{INSTANCE}\{{11111111-2222-3333-4444-555555555555}}"
    env.registry.seed("HKCU", "64-bit", foreign, {"FriendlyName": ("Otra cámara", REG_SZ)})
    before = env.registry.snapshot()
    env.registry.fail_on_write = fail_after
    with pytest.raises(FilterError):
        install_for_user()
    assert env.registry.snapshot() == before


def test_el_rollback_de_una_actualizacion_restaura_la_anterior(env):
    install_for_user(camera_name="Anterior")
    before = env.registry.snapshot()
    env.registry.fail_on_write = 3
    with pytest.raises(FilterError):
        install_for_user(camera_name="Nueva")
    assert env.registry.snapshot() == before


# --------------------------------------------------------------------------- #
# 9-10. Desinstalación
# --------------------------------------------------------------------------- #


def test_desinstalar_quita_lo_propio_y_respeta_lo_ajeno(env):
    foreign = rf"{INSTANCE}\{{11111111-2222-3333-4444-555555555555}}"
    env.registry.seed("HKCU", "64-bit", foreign, {"FriendlyName": ("Otra cámara", REG_SZ)})
    install_for_user()
    uninstall_for_user()
    assert env.registry.read_values("HKCU", "64-bit", foreign) == {"FriendlyName": ("Otra cámara", REG_SZ)}
    for arch, spec in env.specs.items():
        for path in _expected(spec, "", "").keys():
            assert env.registry.read_values("HKCU", arch, path) is None
    # El contenedor de la vista de 32 bits quedó vacío y se quita; el de 64 no.
    assert env.registry.read_values("HKCU", "32-bit", INSTANCE) is None
    assert env.registry.read_values("HKCU", "64-bit", INSTANCE) is not None
    assert not env.user_dir.exists()


def test_desinstalar_dos_veces_no_falla_ni_cambia_nada(env):
    install_for_user()
    uninstall_for_user()
    after = env.registry.snapshot()
    report = uninstall_for_user()
    assert env.registry.snapshot() == after
    assert any("No había" in n for n in report.notes)


def test_desinstalar_sin_haber_instalado_no_toca_nada(env):
    report = uninstall_for_user()
    assert env.registry.log == []
    assert report.removed == []


def test_una_dll_en_uso_no_impide_desinstalar(env, monkeypatch):
    """Si Teams o el navegador tienen la DLL cargada, el registro se quita
    igual y el archivo se informa para borrarlo más tarde."""
    install_for_user()

    def busy(path, *a, **k):  # noqa: ANN001, ANN002, ANN003
        raise PermissionError(f"en uso: {path}")

    monkeypatch.setattr(filters.shutil, "rmtree", busy)
    report = uninstall_for_user()
    assert len(report.locked_files) == 2
    assert report.locked_summary()
    assert all(s.state == "no instalado" for s in user_install_status().values())


# --------------------------------------------------------------------------- #
# 11. Registros existentes del sistema o de otros programas
# --------------------------------------------------------------------------- #


def _seed_system(env, arch: str = "64-bit") -> dict:
    spec = env.specs[arch]
    for path, values in _expected(spec, r"C:\Program Files\X\UnityCaptureFilter64.dll", "Del sistema").items():
        env.registry.seed("HKLM", arch, path, values)
    return env.registry.snapshot()


def test_un_registro_del_sistema_bloquea_la_instalacion_sin_tocar_nada(env):
    before = _seed_system(env)
    with pytest.raises(FilterConflict, match="HKLM"):
        install_for_user()
    assert env.registry.log == []
    assert env.registry.snapshot() == before
    assert not env.user_dir.exists()


def test_un_registro_del_sistema_se_informa_como_solo_lectura(env):
    _seed_system(env)
    status = user_install_status()
    assert status["64-bit"].label == "Registro existente del sistema (solo lectura)"
    assert status["32-bit"].label == "No instalado"
    scopes = {(c.friendly_name, c.scope) for c in list_directshow_cameras()}
    assert ("Del sistema", "sistema") in scopes


def test_desinstalar_no_toca_un_registro_del_sistema(env):
    before = _seed_system(env)
    report = uninstall_for_user()
    assert env.registry.snapshot() == before
    assert any("HKLM" in n for n in report.notes)


def test_un_registro_por_usuario_ajeno_no_se_sobrescribe_ni_se_borra(env):
    spec = env.specs["64-bit"]
    for path, values in _expected(spec, r"C:\Otro\UnityCaptureFilter64.dll", "Unity Video Capture").items():
        env.registry.seed("HKCU", "64-bit", path, values)
    before = env.registry.snapshot()
    with pytest.raises(FilterConflict, match="no es de camvoice"):
        install_for_user()
    uninstall_for_user()
    assert env.registry.snapshot() == before
    assert user_install_status()["64-bit"].state == "ajeno"


# --------------------------------------------------------------------------- #
# 13. Rutas con espacios y acentos
# --------------------------------------------------------------------------- #


def test_rutas_con_espacios_y_acentos(env):
    report = install_for_user(camera_name="Cámara de Ñandú")
    for dll in report.installed.values():
        assert "José Ñúñez" in dll
        assert filters._is_ours(dll)
    assert all(s.state == "instalado" for s in user_install_status().values())


def test_una_ruta_fuera_de_la_carpeta_no_es_nuestra(env, tmp_path):
    assert not filters._is_ours(str(tmp_path / "UnityCaptureFilter64.dll"))
    # Un prefijo de texto no basta: «filters-otro» no está dentro de «filters».
    assert not filters._is_ours(str(env.user_dir) + "-otro\\x.dll")
    assert not filters._is_ours("")


# --------------------------------------------------------------------------- #
# Enumeración y clasificación
# --------------------------------------------------------------------------- #


def test_la_enumeracion_distingue_usuario_y_sistema(env):
    install_for_user(camera_name="Mía")
    env.registry.seed("HKLM", "64-bit", rf"{INSTANCE}\{{A3FCE0F5-3493-419F-958A-ABA1250EC20B}}",
                      {"FriendlyName": ("OBS Virtual Camera", REG_SZ),
                       "CLSID": ("{A3FCE0F5-3493-419F-958A-ABA1250EC20B}", REG_SZ)})
    found = {(c.friendly_name, c.architecture, c.scope) for c in list_directshow_cameras()}
    assert found == {("Mía", "64-bit", "usuario"), ("Mía", "32-bit", "usuario"),
                     ("OBS Virtual Camera", "64-bit", "sistema")}
    mine = [c for c in list_directshow_cameras() if c.scope == "usuario"]
    assert all(c.dll_exists and c.backend == "unitycapture" for c in mine)
    assert "usuario" in mine[0].label


def test_el_resumen_muestra_el_estado_por_usuario(env):
    assert "No instalado" in describe_availability()
    install_for_user()
    assert "Instalado para este usuario" in describe_availability()


def test_reconoce_el_filtro_de_obs_por_su_clsid():
    cam = RegisteredCamera("Lo que sea", "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}", "x.dll", "64-bit")
    assert cam.backend == "obs"


def test_reconoce_unitycapture_por_el_nombre():
    cam = RegisteredCamera("Unity Video Capture", "{0000}", "x.dll", "64-bit")
    assert cam.backend == "unitycapture"


def test_reconoce_el_filtro_propio_por_su_clsid_aunque_se_renombre():
    cam = RegisteredCamera("Lo que el usuario quiso", "{5C2CD55C-92AD-4999-8666-912BD3E70020}", "x.dll", "32-bit")
    assert cam.backend == "unitycapture"


def test_una_camara_fisica_no_declara_backend():
    cam = RegisteredCamera("Integrated Webcam", "{0002}", "usbvideo.dll", "64-bit")
    assert cam.backend == ""


def test_detecta_un_registro_huerfano(tmp_path):
    """Una DLL que ya no existe deja la entrada del registro pero no sirve."""
    cam = RegisteredCamera("Unity Video Capture", "{0003}", str(tmp_path / "no.dll"), "64-bit")
    assert not cam.dll_exists
    assert "AUSENTE" in cam.label


def test_el_directorio_de_filtros_esta_definido():
    assert isinstance(filters_dir(), Path)


def test_la_carpeta_de_usuario_esta_en_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("CAMVOICE_FILTERS_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert filters.user_filters_dir() == tmp_path / "camvoice" / "filters"


def test_las_dll_incluidas_se_clasifican_por_arquitectura(monkeypatch, tmp_path):
    (tmp_path / "FiltroCamvoice64.dll").write_bytes(b"MZ")
    (tmp_path / "FiltroCamvoice32.dll").write_bytes(b"MZ")
    monkeypatch.setattr("camvoice.output.filters.filters_dir", lambda: tmp_path)
    found = bundled_filters()
    assert set(found) == {"64-bit", "32-bit"}


def test_las_dll_incluidas_existen_y_son_binarios_pe():
    found = bundled_filters()
    if not found:
        pytest.skip("este árbol no incluye las DLL del filtro")
    assert set(found) == {"64-bit", "32-bit"}
    for path in found.values():
        assert path.read_bytes()[:2] == b"MZ", f"{path.name} no es un binario PE"


@windows_only
def test_la_enumeracion_del_registro_real_funciona():
    for cam in list_directshow_cameras():
        assert cam.clsid.startswith("{")
        assert cam.architecture in ("64-bit", "32-bit")
        assert cam.scope in ("usuario", "sistema")


@windows_only
def test_los_backends_detectados_son_conocidos():
    for backend in detect_backends():
        assert backend in ("obs", "unitycapture")
