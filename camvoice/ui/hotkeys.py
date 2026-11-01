"""Atajos de teclado globales para los gestos (Windows, RegisterHotKey).

Por qué RegisterHotKey y no un gancho de teclado:

* **No espía el teclado.** Windows solo avisa cuando se pulsa exactamente una
  combinación registrada; el resto de pulsaciones no pasa por la aplicación.
  Un gancho global (`SetWindowsHookEx`) vería todo lo que se escribe.
* **No roba atajos ajenos.** Si otra aplicación ya registró la combinación,
  el registro falla y se informa al usuario, en vez de quitársela.
* `MOD_NOREPEAT`: mantener pulsada la tecla no dispara el gesto en ráfaga.

Los atajos se registran como *atajos de hilo* (hwnd NULL) desde el hilo de
la interfaz: Windows deja `WM_HOTKEY` en la cola de ese hilo, Qt la entrega a
los filtros de eventos nativos y `HotkeyManager.handle` invoca el gesto. El
gesto en sí lo ejecuta el director en el hilo de render; aquí solo se pide.

Las reglas de qué combinaciones se aceptan (ni AltGr, ni atajos de Teams, ni
la tecla Windows) están en `camvoice/gestures.py::validate_hotkey`.

La API de Win32 se inyecta (`HotkeyApi`), de modo que la lógica se prueba sin
registrar nada en el sistema.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..gestures import CANCEL, HotkeyError, gesture_label, validate_hotkey

__all__ = [
    "HotkeyApi",
    "Win32HotkeyApi",
    "HotkeyBinding",
    "HotkeyManager",
    "install_native_filter",
]

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
_FIRST_ID = 0xB100
"""Identificadores propios (0x0000-0xBFFF es el rango de una aplicación)."""


class HotkeyApi(Protocol):
    def register(self, hk_id: int, modifiers: int, vk: int) -> tuple[bool, int]:
        """Registra; devuelve (éxito, código de error de Windows)."""

    def unregister(self, hk_id: int) -> None: ...


class Win32HotkeyApi:
    """RegisterHotKey/UnregisterHotKey de user32 (solo Windows)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._register = user32.RegisterHotKey
        self._register.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._register.restype = wintypes.BOOL
        self._unregister = user32.UnregisterHotKey
        self._unregister.argtypes = [wintypes.HWND, ctypes.c_int]
        self._unregister.restype = wintypes.BOOL

    def register(self, hk_id: int, modifiers: int, vk: int) -> tuple[bool, int]:
        ok = bool(self._register(None, hk_id, modifiers | MOD_NOREPEAT, vk))
        return ok, (0 if ok else self._ctypes.get_last_error())

    def unregister(self, hk_id: int) -> None:
        self._unregister(None, hk_id)


@dataclass
class HotkeyBinding:
    name: str
    """Gesto (o `neutro` para cancelar)."""
    text: str
    hk_id: int = 0
    """0 si el texto no es válido o repite otro atajo: nunca se registra."""
    registered: bool = False
    invalid: str = ""
    """Problema del propio texto (reglas, duplicado). Persiste."""
    os_error: str = ""
    """Problema al registrarlo en Windows (ocupado). Se reintenta al reanudar."""

    @property
    def problem(self) -> str:
        return self.invalid or self.os_error


class HotkeyManager:
    """Registra los atajos de los gestos y despacha las pulsaciones.

    `on_trigger(nombre)` se llama en el hilo de la interfaz. Suspender quita
    los registros del sistema (las combinaciones vuelven a quedar libres para
    las demás aplicaciones), no solo ignora las pulsaciones.
    """

    def __init__(self, on_trigger: Callable[[str], None], api: HotkeyApi | None = None) -> None:
        self._on_trigger = on_trigger
        self._api = api
        self._bindings: list[HotkeyBinding] = []
        self._by_id: dict[int, HotkeyBinding] = {}
        self._wanted: dict[str, str] = {}
        self._suspended = False
        self.last_trigger: tuple[str, float] | None = None
        """(gesto, time.perf_counter()) de la última pulsación atendida."""

    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return self._api is not None

    @property
    def suspended(self) -> bool:
        return self._suspended

    @property
    def bindings(self) -> list[HotkeyBinding]:
        return list(self._bindings)

    def problems(self) -> list[str]:
        return [b.problem for b in self._bindings if b.problem]

    def describe(self) -> str:
        """Resumen de una línea para la interfaz."""
        if not self.available:
            return "Atajos globales no disponibles en este sistema."
        if self._suspended:
            return "Atajos suspendidos: las combinaciones quedan libres para otras aplicaciones."
        activos = [b for b in self._bindings if b.registered]
        if not activos:
            return "Ningún atajo activo."
        return "Atajos activos: " + ", ".join(
            f"{b.text} → {gesture_label(b.name)}" for b in activos
        )

    # ------------------------------------------------------------------ #

    def apply(self, wanted: dict[str, str]) -> list[str]:
        """Sustituye todos los atajos por `wanted` (gesto -> texto).

        Devuelve los problemas encontrados; los atajos válidos se registran
        igualmente. Un atajo vacío deja el gesto sin atajo (solo botón).
        """
        self._wanted = {k: (v or "").strip() for k, v in wanted.items()}
        self._unregister_all()
        self._bindings = []
        seen: dict[tuple[int, int], str] = {}
        next_id = _FIRST_ID
        for name, text in self._wanted.items():
            if not text:
                continue
            binding = HotkeyBinding(name, text)
            self._bindings.append(binding)
            try:
                hk = validate_hotkey(text)
            except HotkeyError as exc:
                binding.invalid = f"{gesture_label(name)}: {exc}"
                continue
            binding.text = hk.text
            key = (hk.modifiers, hk.vk)
            if key in seen:
                binding.invalid = (
                    f"{hk.text} está asignado a «{gesture_label(seen[key])}» y a "
                    f"«{gesture_label(name)}»; solo vale para el primero."
                )
                continue
            seen[key] = name
            binding.hk_id = next_id
            next_id += 1
        if not self._suspended:
            self._register_all()
        return self.problems()

    def suspend(self) -> None:
        if self._suspended:
            return
        self._suspended = True
        self._unregister_all()

    def resume(self) -> list[str]:
        if not self._suspended:
            return self.problems()
        self._suspended = False
        self._register_all()
        return self.problems()

    def set_enabled(self, enabled: bool) -> list[str]:
        if enabled:
            return self.resume()
        self.suspend()
        return []

    def clear(self) -> None:
        self._unregister_all()
        self._bindings = []
        self._wanted = {}

    def handle(self, hk_id: int) -> bool:
        """Despacha una pulsación (WM_HOTKEY). True si era nuestra."""
        binding = self._by_id.get(int(hk_id))
        if binding is None or self._suspended:
            return False
        self.last_trigger = (binding.name, time.perf_counter())
        log.info("Atajo %s → %s", binding.text, binding.name)
        try:
            self._on_trigger(binding.name)
        except Exception:  # pragma: no cover - un fallo no debe tumbar la interfaz
            log.exception("Fallo al atender el atajo %s", binding.text)
        return True

    # ------------------------------------------------------------------ #

    def _register_all(self) -> None:
        self._by_id = {}
        if self._api is None:
            return
        for b in self._bindings:
            if not b.hk_id:
                continue
            hk = validate_hotkey(b.text)
            ok, err = self._api.register(b.hk_id, hk.modifiers, hk.vk)
            b.registered = ok
            b.os_error = ""
            if ok:
                self._by_id[b.hk_id] = b
            elif err == ERROR_HOTKEY_ALREADY_REGISTERED:
                b.os_error = (
                    f"{b.text} ({gesture_label(b.name)}) está ocupado por otra "
                    "aplicación. Elige otra combinación en la pestaña Gestos."
                )
            else:
                b.os_error = f"{b.text} ({gesture_label(b.name)}): Windows rechazó el atajo (error {err})."

    def _unregister_all(self) -> None:
        if self._api is not None:
            for b in self._bindings:
                if b.registered:
                    try:
                        self._api.unregister(b.hk_id)
                    except Exception:  # pragma: no cover
                        log.exception("No se pudo liberar %s", b.text)
                b.registered = False
                b.os_error = ""
        self._by_id = {}


def default_api() -> HotkeyApi | None:
    """La API real en Windows; None en otros sistemas (atajos no disponibles).

    `CAMVOICE_NO_GLOBAL_HOTKEYS=1` la desactiva (pruebas, o si se prefiere
    usar solo los botones y la bandeja)."""
    if sys.platform != "win32" or os.environ.get("CAMVOICE_NO_GLOBAL_HOTKEYS"):
        return None
    try:
        return Win32HotkeyApi()
    except OSError:  # pragma: no cover
        return None


def install_native_filter(app, manager: HotkeyManager):  # noqa: ANN001, ANN201
    """Instala en la QApplication el filtro que entrega WM_HOTKEY al gestor.

    Devuelve el filtro (hay que conservar la referencia mientras viva la
    aplicación) o None fuera de Windows.
    """
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    from PySide6.QtCore import QAbstractNativeEventFilter

    class _Filter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):  # noqa: N802, ANN001, ANN201
            try:
                if bytes(event_type) == b"windows_generic_MSG":
                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY and manager.handle(int(msg.wParam)):
                        return True, 0
            except Exception:  # pragma: no cover - nunca romper el bucle de eventos
                log.exception("Filtro de atajos")
            return False, 0

    filt = _Filter()
    app.installNativeEventFilter(filt)
    return filt


__all__ += ["default_api", "CANCEL"]
