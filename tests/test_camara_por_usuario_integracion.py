"""Integración real: registro por usuario, DirectShow y fotogramas.

Escribe en HKCU de quien la ejecuta y abre la cámara de verdad, así que no
corre por defecto. Para lanzarla, en una sesión **sin elevar**:

    $env:CAMVOICE_TEST_REAL_CAMERA = "1"
    .venv\\Scripts\\python.exe -m pytest tests/test_camara_por_usuario_integracion.py -v

Si la cámara ya estaba instalada para el usuario se salta, para no cambiar su
estado. Al terminar desinstala lo que instaló.
"""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="solo Windows"),
    pytest.mark.skipif(
        os.environ.get("CAMVOICE_TEST_REAL_CAMERA") != "1",
        reason="integración real: definir CAMVOICE_TEST_REAL_CAMERA=1",
    ),
]

NAME = "CamVoice Prueba Integracion"


@pytest.fixture(scope="module")
def instalada():
    from camvoice.cli import _is_elevated
    from camvoice.output import filters

    if _is_elevated():
        pytest.skip("proceso elevado: COM ignora HKCU; repetir sin elevar")
    states = {s.state for s in filters.user_install_status().values()}
    if states != {"no instalado"}:
        pytest.skip(f"estado previo distinto de «no instalado» ({states}); no se toca")
    report = filters.install_for_user(camera_name=NAME)
    yield report
    filters.uninstall_for_user()


def test_registro_solo_en_hkcu(instalada):
    from camvoice.output import filters

    for status in filters.user_install_status().values():
        assert status.state == "instalado", status.problems
    for cam in filters.list_directshow_cameras():
        if cam.friendly_name == NAME:
            assert cam.scope == "usuario"


def test_directshow_64_la_enumera(instalada):
    from camvoice.output.dshow import enumerate_video_inputs
    from camvoice.output.filters import UNITYCAPTURE_FILTERS

    hits = [d for d in enumerate_video_inputs() if d.friendly_name == NAME]
    assert hits and hits[0].clsid == UNITYCAPTURE_FILTERS["64-bit"].filter_clsid


def test_directshow_32_la_enumera(instalada):
    from camvoice.output.dshow import DShowError, enumerate_video_inputs_x86
    from camvoice.output.filters import UNITYCAPTURE_FILTERS

    try:
        devices = enumerate_video_inputs_x86()
    except DShowError as exc:
        pytest.skip(f"No verificable: {exc}")
    hits = [d for d in devices if d.friendly_name == NAME]
    assert hits and hits[0].clsid == UNITYCAPTURE_FILTERS["32-bit"].filter_clsid


def test_entrega_fotogramas(instalada):
    from camvoice.output.dshow import roundtrip

    result = roundtrip(NAME, seconds=4)
    assert result.ok, result.error


def test_desinstalar_la_quita_de_directshow(instalada):
    from camvoice.output import filters
    from camvoice.output.dshow import enumerate_video_inputs

    filters.uninstall_for_user()
    assert NAME not in {d.friendly_name for d in enumerate_video_inputs()}
    assert {s.state for s in filters.user_install_status().values()} == {"no instalado"}
    filters.uninstall_for_user()  # idempotente
