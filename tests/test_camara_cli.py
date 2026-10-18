"""Códigos de salida de `install-camera` y `verify-camera`.

Se ejecutan en el mismo proceso, con el registro en memoria de
`test_filters.py` y una enumeración DirectShow simulada que lee ese mismo
registro: así se comprueba la lógica de la CLI sin tocar la máquina.
"""

from __future__ import annotations

import pytest

from camvoice import cli
from camvoice.output import dshow, filters
from camvoice.output.dshow import DirectShowDevice, DShowError, RoundtripResult
from tests.test_filters import _expected, env  # noqa: F401  (fixture)


def _seen(arch: str):
    def enumerate_() -> list[DirectShowDevice]:
        return [
            DirectShowDevice(i, c.friendly_name, c.clsid)
            for i, c in enumerate(filters.list_directshow_cameras())
            if c.architecture == arch
        ]
    return enumerate_


@pytest.fixture
def dshow_fake(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dshow, "enumerate_video_inputs", _seen("64-bit"))
    monkeypatch.setattr(dshow, "enumerate_video_inputs_x86", _seen("32-bit"))
    monkeypatch.setattr(cli, "_process_bits", lambda: 64)
    monkeypatch.setattr(cli, "_is_elevated", lambda: False)

    def roundtrip(name, seconds=4.0, **_):  # noqa: ANN001, ANN003, ANN202
        return RoundtripResult(name, True, 1920, 1080, 60, 58, 6, "")

    monkeypatch.setattr(dshow, "roundtrip", roundtrip)
    return env


def run(*argv: str) -> int:
    return cli.main(list(argv))


def test_instalar_devuelve_0_y_dice_que_es_por_usuario(dshow_fake, capsys):
    assert run("install-camera") == 0
    out = capsys.readouterr().out
    assert "solo para el usuario" in out
    assert "DirectShow (64 bits) enumera" in out


def test_instalar_dos_veces_devuelve_0(dshow_fake):
    assert run("install-camera") == 0
    assert run("install-camera") == 0


def test_un_conflicto_con_el_sistema_devuelve_2(dshow_fake, capsys):
    spec = dshow_fake.specs["64-bit"]
    for path, values in _expected(spec, r"C:\Program Files\X\UnityCaptureFilter64.dll", "X").items():
        dshow_fake.registry.seed("HKLM", "64-bit", path, values)
    assert run("install-camera") == 2
    assert "HKLM" in capsys.readouterr().out
    assert dshow_fake.registry.log == []


def test_un_error_devuelve_1(dshow_fake, tmp_path, capsys):
    assert run("install-camera", "--dll", str(tmp_path / "no-existe.dll")) == 1
    assert "[--]" in capsys.readouterr().out


def test_si_directshow_no_la_enumera_devuelve_1(dshow_fake, monkeypatch, capsys):
    monkeypatch.setattr(dshow, "enumerate_video_inputs", lambda: [])
    assert run("install-camera") == 1
    assert "no enumera" in capsys.readouterr().out


def test_si_no_se_puede_enumerar_devuelve_1(dshow_fake, monkeypatch):
    def broken():  # noqa: ANN202
        raise DShowError("CoCreateInstance falló")

    monkeypatch.setattr(dshow, "enumerate_video_inputs", broken)
    assert run("install-camera") == 1


def test_desinstalar_devuelve_0_aunque_no_hubiera_nada(dshow_fake, capsys):
    assert run("install-camera", "--uninstall") == 0
    assert run("install-camera", "--uninstall") == 0
    assert "ya no enumera" in capsys.readouterr().out


def test_verificar_tras_instalar_devuelve_0(dshow_fake, capsys):
    assert run("install-camera") == 0
    capsys.readouterr()
    assert run("verify-camera") == 0
    out = capsys.readouterr().out
    assert "Instalado para este usuario" in out
    assert "64-bit: un proceso de 64 bits la ve" in out
    assert "32-bit: un proceso de 32 bits la ve" in out
    assert "58/60" in out
    assert "No verificable" in out  # los fotogramas de 32 bits no se comprueban


def test_verificar_sin_instalar_devuelve_1(dshow_fake, capsys):
    assert run("verify-camera", "--no-frames") == 1
    assert "No instalado" in capsys.readouterr().out


def test_verificar_con_la_dll_alterada_devuelve_1(dshow_fake, capsys):
    from pathlib import Path

    run("install-camera")
    status = filters.user_install_status()["64-bit"]
    Path(status.dll_path).write_bytes(b"MZ manipulada")
    capsys.readouterr()
    assert run("verify-camera", "--no-frames") == 1
    assert "NO COINCIDE" in capsys.readouterr().out


def test_lo_no_verificable_no_cuenta_como_fallo(dshow_fake, monkeypatch, capsys):
    """Sin PowerShell de 32 bits, la enumeración x86 es «No verificable», no un fallo."""
    def unverifiable():  # noqa: ANN202
        raise DShowError("No verificable: no hay SysWOW64")

    run("install-camera")
    monkeypatch.setattr(dshow, "enumerate_video_inputs_x86", unverifiable)
    capsys.readouterr()
    assert run("verify-camera", "--no-frames") == 0
    assert "32-bit: No verificable" in capsys.readouterr().out


def test_unos_fotogramas_erroneos_devuelven_1(dshow_fake, monkeypatch, capsys):
    run("install-camera")
    monkeypatch.setattr(
        dshow, "roundtrip",
        lambda name, **_: RoundtripResult(name, True, 640, 360, 40, 1, 0, "Los colores no coinciden"),
    )
    capsys.readouterr()
    assert run("verify-camera") == 1
    assert "RESULTADO: FALLO" in capsys.readouterr().out
