"""Pruebas de la interfaz de línea de comandos como proceso.

Se ejecutan en un subproceso a propósito: comprueban que el punto de entrada
arranca de verdad. Importar el módulo no basta, porque los dos fallos reales que
se dieron al empaquetar —la importación relativa en el script de entrada y
`sys.stdout` en None— solo aparecen al ejecutar el programa.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from camvoice.cli import build_parser


def run_cli(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "camvoice", *args],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )


# --------------------------------------------------------------------------- #
# Punto de entrada
# --------------------------------------------------------------------------- #


def test_el_modulo_se_puede_ejecutar():
    """`python -m camvoice --help` debe funcionar.

    Esta prueba habría detectado la importación relativa que rompía el
    ejecutable empaquetado.
    """
    result = run_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "camvoice" in result.stdout


def entry_point_source() -> str:
    return (Path(__file__).parent.parent / "camvoice" / "__main__.py").read_text(
        encoding="utf-8"
    )


def test_el_punto_de_entrada_no_usa_importaciones_relativas():
    """PyInstaller ejecuta __main__.py sin paquete padre.

    Con una importación relativa el ejecutable falla al arrancar con «attempted
    relative import with no known parent package», y en modo ventana el error
    aparece como un diálogo modal que cuelga el programa.

    Se analiza el árbol sintáctico y no el texto: los comentarios del propio
    archivo explican el problema y mencionan la forma incorrecta.
    """
    tree = ast.parse(entry_point_source())
    relative = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0
    ]
    assert not relative, (
        "importaciones relativas en el punto de entrada: "
        + ", ".join(f"nivel {n.level} desde {n.module}" for n in relative)
    )
    assert "camvoice.cli" in {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }


def test_el_punto_de_entrada_protege_los_flujos_de_salida():
    """En modo ventana PyInstaller deja sys.stdout en None."""
    assert "devnull" in entry_point_source()


# --------------------------------------------------------------------------- #
# Subcomandos
# --------------------------------------------------------------------------- #


def test_todos_los_subcomandos_tienen_ayuda():
    parser = build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    assert actions, "no se registró ningún subcomando"
    names = set(actions[0].choices)
    assert {
        "gui", "doctor", "devices", "probe", "prepare",
        "run", "bench", "vad-test", "make-clips", "config", "clean",
    } <= names


@pytest.mark.parametrize(
    "command",
    ["doctor", "devices", "probe", "prepare", "run", "bench", "vad-test", "make-clips"],
)
def test_la_ayuda_de_cada_subcomando_se_genera(command):
    result = run_cli(command, "--help")
    assert result.returncode == 0, result.stderr


def test_probe_informa_de_un_archivo_que_no_es_video(tmp_path):
    """CA-03: mensaje comprensible, código de salida distinto de cero."""
    bad = tmp_path / "falso.mp4"
    bad.write_bytes(b"no soy un video" * 100)
    result = run_cli("probe", str(bad))
    assert result.returncode == 1
    assert "falso.mp4" in result.stdout


def test_probe_lee_un_clip_generado(tiny_clips):
    speaking, silent = tiny_clips
    result = run_cli("probe", str(speaking), str(silent))
    assert result.returncode == 0, result.stderr
    assert "64x48" in result.stdout


def test_config_muestra_la_configuracion(tmp_path):
    result = run_cli("--config", str(tmp_path / "c.json"), "config")
    assert result.returncode == 0, result.stderr
    assert '"fps": 15' in result.stdout


def test_prepare_falla_de_forma_limpia_sin_clips(tmp_path):
    result = run_cli("--config", str(tmp_path / "c.json"), "prepare")
    assert result.returncode == 1
    assert "clip" in result.stdout.lower()


def test_run_con_duracion_cero_de_clips_sinteticos(tiny_clips, tmp_path):
    """Recorrido completo sin cámara virtual: prepara, arranca y para."""
    speaking, silent = tiny_clips
    result = run_cli(
        "--config", str(tmp_path / "c.json"), "run",
        "--speaking", str(speaking), "--silent", str(silent),
        "--no-virtual-cam", "--duration", "3", "--quiet",
        timeout=180,
    )
    assert result.returncode == 0, result.stderr

    # Que el contador sea mayor que cero, no solo que la línea exista: la
    # primera versión leía el estado después de descartar el motor y siempre
    # informaba 0 fotogramas tras una ejecución correcta.
    emitted = next(
        line for line in result.stdout.splitlines() if "fotogramas emitidos" in line
    )
    count = int(emitted.rsplit(":", 1)[1].strip())
    assert count > 10, f"solo se emitieron {count} fotogramas en 3 s"


# --------------------------------------------------------------------------- #
# Cámaras registradas en el sistema
# --------------------------------------------------------------------------- #


def test_cameras_enumera_el_registro():
    """En Windows siempre hay al menos una entrada si hay cualquier cámara."""
    result = run_cli("cameras")
    assert result.returncode in (0, 1)
    assert "C" in result.stdout  # produjo salida legible
    assert "Backends utilizables" in result.stdout or "No se pudo enumerar" in result.stdout


def test_la_salida_redirigida_va_en_utf8():
    """Los acentos deben sobrevivir a una tubería, no solo a la consola.

    Sin forzar UTF-8, Python usa la codificación ANSI del sistema al redirigir
    y «Micrófono» llega como «Micr?fono» a cualquier script que capture la
    salida o a un informe guardado en un archivo.
    """
    raw = subprocess.run(
        [sys.executable, "-m", "camvoice", "devices"],
        capture_output=True, timeout=120,
    )
    texto = raw.stdout.decode("utf-8", errors="strict")
    assert "disponibles" in texto


def test_el_punto_de_entrada_fuerza_utf8():
    assert "utf-8" in entry_point_source().lower()
