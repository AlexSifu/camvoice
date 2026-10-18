"""Punto de entrada: `python -m camvoice` y el ejecutable empaquetado."""

import os
import sys


def _ensure_streams() -> None:
    """Deja sys.stdout y sys.stderr existentes y en UTF-8.

    Dos problemas distintos, ambos específicos de Windows:

    1. **Flujos inexistentes.** PyInstaller, al construir en modo ventana
       (`console=False`), no asigna consola al proceso y deja `sys.stdout` y
       `sys.stderr` en None. Cualquier `print` revienta entonces con
       AttributeError y, como el proceso es gráfico, el fallo aparece como un
       diálogo modal «Unhandled exception in script» que cuelga el programa.
       Para los subcomandos de consola está `camvoice-cli.exe`.

    2. **Codificación al redirigir.** Cuando la salida va a una tubería o a un
       archivo en lugar de a la consola, Python usa la codificación ANSI del
       sistema (cp1252 en español) y no UTF-8. El resultado es que «Micrófono»
       llega como «Micr?fono» a cualquiera que capture la salida: un script de
       PowerShell, un informe guardado con `3-Diagnostico.ps1 -Guardar` o un
       `> salida.txt`. Forzar UTF-8 lo corrige en todos los casos.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # flujo ya cerrado o sin soporte: no es motivo de fallo


_ensure_streams()

# Importación ABSOLUTA, no relativa, a propósito. PyInstaller ejecuta este
# archivo como script de entrada, es decir con el nombre `__main__` y sin
# paquete padre: ahí `from .cli import main` falla con «attempted relative
# import with no known parent package». La forma absoluta funciona en los dos
# casos, porque tanto `python -m camvoice` como el ejecutable empaquetado tienen
# el paquete `camvoice` disponible para importar.
from camvoice.cli import main  # noqa: E402 - después de asegurar los flujos

if __name__ == "__main__":
    raise SystemExit(main())
