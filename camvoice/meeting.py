"""Estado del micrófono de la reunión (Teams de escritorio): lectura, nunca control.

El mute de Teams es *de aplicación*: el micrófono físico sigue entregando audio
a CamVoice aunque Teams no lo transmita. Para que el vídeo de habla no se
emita mientras la persona conversa con el micrófono de la reunión silenciado,
hace falta leer ese estado real; el nivel RMS, el VAD o el mute global de
Windows no lo saben.

## Cómo se lee

Con **Windows UI Automation** (UIA) en modo solo lectura, mediante COM puro
con `ctypes` (sin dependencias nuevas):

1. Se enumeran las ventanas visibles cuyo proceso es `ms-teams.exe` (Teams
   nuevo; también `Teams.exe` clásico). Sin ventana → `NO_MEETING`.
2. En cada ventana se busca el control con `AutomationId = microphone-button`
   (observado en versiones recientes de Teams; se verifica en cada lectura, no
   se da por hecho). Ninguno → `NO_MEETING`; más de uno visible → `UNKNOWN`
   (no se adivina cuál reunión manda).
3. La etiqueta de accesibilidad del botón describe la **acción disponible**:
   «Mute mic» / «Silenciar» significa que el micrófono está activo
   (`UNMUTED`); «Unmute mic» / «Activar micrófono» significa que está en
   silencio (`MUTED`). Texto ambiguo → `UNKNOWN`.

Todo ocurre en un hilo propio con apartamento COM multihilo, fuera del hilo de
render y de los callbacks de audio. Se fijan tiempos de espera de UIA para que
un Teams colgado no bloquee la lectura indefinidamente, y el elemento hallado
se vuelve a buscar periódicamente (WebView2 reconstruye su árbol).

## Garantías de seguridad del estado

* `UNMUTED` **caduca**: si la última lectura tiene más de `unmuted_expiry_ms`
  (450 ms por defecto), `snapshot()` devuelve `UNKNOWN`. El bucle lee cada
  250 ms y una lectura sana tarda decenas de milisegundos, así que 450 ms
  tolera una lectura lenta pero nunca dos periodos perdidos: un Teams que se
  cuelga, se cierra o cambia su interfaz deja de autorizar habla en menos de
  medio segundo, parecido al tiempo de liberación de la puerta de voz.
* Cualquier error, lectura caducada o hilo detenido se publica como `UNKNOWN`.
* Nunca se pulsa, enfoca ni modifica nada en Teams; no se requiere
  administrador ni conexión. UIA no es una API oficial de Teams: puede dejar
  de funcionar con una actualización, y entonces el resultado es `UNKNOWN`.

Los registros (opt-in, `logging` «camvoice.meeting») solo contienen estados y
códigos de error, nunca títulos de ventana ni contenido de la reunión.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum

__all__ = [
    "MeetingState",
    "MeetingSnapshot",
    "MeetingMuteProvider",
    "NullMeetingProvider",
    "TeamsUiaProvider",
    "classify_mic_label",
    "default_provider",
    "state_label",
]

log = logging.getLogger("camvoice.meeting")

TEAMS_PROCESSES: tuple[str, ...] = ("ms-teams.exe", "teams.exe", "msteams.exe")
MIC_AUTOMATION_ID = "microphone-button"
SOURCE_TEAMS_UIA = "teams-uia"


class MeetingState(Enum):
    NO_MEETING = "NO_MEETING"
    """Teams no está visible o no hay ningún control de micrófono de reunión."""

    MUTED = "MUTED"
    UNMUTED = "UNMUTED"
    UNKNOWN = "UNKNOWN"
    """Error, carga, varias reuniones, texto ambiguo o lectura caducada."""


_STATE_LABELS = {
    MeetingState.NO_MEETING: "sin reunión",
    MeetingState.MUTED: "micrófono silenciado",
    MeetingState.UNMUTED: "micrófono activo",
    MeetingState.UNKNOWN: "estado desconocido",
}


def state_label(state: MeetingState) -> str:
    return _STATE_LABELS.get(state, state.value)


@dataclass(frozen=True, slots=True)
class MeetingSnapshot:
    """Última lectura publicada. `detail` es técnico y sin datos personales."""

    state: MeetingState = MeetingState.UNKNOWN
    t_monotonic: float = 0.0
    """Instante (time.monotonic) de la lectura; 0 = nunca se leyó."""

    source: str = ""
    detail: str = ""
    label: str = ""
    """Etiqueta de accesibilidad del botón de micrófono (texto fijo de la
    interfaz de Teams, p. ej. «Mute mic»); vacío si no se leyó."""

    windows: int = 0
    """Ventanas visibles de Teams en la última exploración."""

    buttons: int = 0
    """Controles de micrófono hallados en la última exploración."""

    read_ms: float = 0.0
    """Duración de la última lectura (diagnóstico de carga)."""

    def age_ms(self, now: float | None = None) -> float:
        if self.t_monotonic <= 0:
            return float("inf")
        return ((time.monotonic() if now is None else now) - self.t_monotonic) * 1000.0

    @property
    def reliable(self) -> bool:
        """MUTED/UNMUTED/NO_MEETING leídos de verdad (no UNKNOWN)."""
        return self.state is not MeetingState.UNKNOWN

    def describe(self) -> str:
        texto = f"Teams: {state_label(self.state)}"
        if self.detail:
            texto += f" ({self.detail})"
        return texto


# --------------------------------------------------------------------------- #
# Interpretación de la etiqueta del botón
# --------------------------------------------------------------------------- #


def _fold(text: str) -> str:
    """Minúsculas sin acentos, espacios normalizados."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain.lower()).strip()


# Frases que indican que el micrófono está EN SILENCIO ahora (la acción
# disponible es activarlo) y frases que indican que está ACTIVO (la acción es
# silenciarlo). Se evalúan sobre el texto plegado (sin acentos). Las formas de
# estado («muted», «desactivado») se comprueban antes que las de acción porque
# contienen a las otras («unmute» ⊂ «unmuted», «activar» ⊂ «desactivar»).
_MUTED_NOW = (
    r"\bmuted\b(?!\s*:\s*(no|false|off))",
    r"\bunmute\b",
    r"\bturn on (the )?(mic|microphone)\b",
    r"\bmic(rophone)? (is )?off\b",
    r"\bquitar (el )?silencio\b",
    r"\bdesactivar (el )?silencio\b",
    r"\bdejar de silenciar\b",
    r"\breactivar\b",
    r"\bactivar\b",
    r"\bencender\b",
    r"\bsilenciado\b",
    r"\bdesactivado\b",
    r"\bapagado\b",
)
_UNMUTED_NOW = (
    r"\bunmuted\b",
    r"\bmute\b",
    r"\bturn off (the )?(mic|microphone)\b",
    r"\bmic(rophone)? (is )?on\b",
    r"\bsilenciar\b",
    r"\bdesactivar\b",
    r"\bapagar\b",
    r"\bactivado\b",
    r"\bencendido\b",
)
_MUTED_RE = tuple(re.compile(p) for p in _MUTED_NOW)
_UNMUTED_RE = tuple(re.compile(p) for p in _UNMUTED_NOW)


def classify_mic_label(label: str) -> MeetingState:
    """Deduce el estado actual a partir de la etiqueta del botón.

    La etiqueta describe la acción disponible, no el estado: «Mute mic» ⇒
    micrófono activo (UNMUTED); «Unmute mic» ⇒ en silencio (MUTED). Si el
    texto encaja con ambas lecturas, con ninguna o está vacío → UNKNOWN.
    """
    text = _fold(label)
    if not text:
        return MeetingState.UNKNOWN
    # Orden: estado («unmuted»/«muted») antes que acción («unmute»/«mute»).
    if re.search(r"\bunmuted\b", text):
        says_muted, says_unmuted = False, True
    elif re.search(r"\bmuted\b", text):
        says_muted, says_unmuted = True, False
    else:
        # Primero las frases compuestas de "quitar silencio"; si aparecen,
        # la palabra «silenciar»/«desactivar» que contienen no cuenta.
        compound = [p for p in _MUTED_RE[4:7] if p.search(text)]
        if compound:
            says_muted, says_unmuted = True, False
        else:
            says_muted = any(p.search(text) for p in _MUTED_RE)
            says_unmuted = any(p.search(text) for p in _UNMUTED_RE)
            # «activar» aparece dentro de «desactivar»/«reactivar»: la
            # expresión \bactivar\b no casa ahí, pero «desactivar micrófono»
            # (acción) y «activar micrófono» (acción) son opuestas y ya están
            # repartidas; solo queda resolver textos que mezclen ambas.
    if says_muted == says_unmuted:
        return MeetingState.UNKNOWN
    return MeetingState.MUTED if says_muted else MeetingState.UNMUTED


# --------------------------------------------------------------------------- #
# Proveedores
# --------------------------------------------------------------------------- #


class MeetingMuteProvider:
    """Interfaz común. `snapshot()` siempre es barato y seguro desde cualquier hilo."""

    name = "none"

    def start(self) -> None:  # pragma: no cover - interfaz
        pass

    def stop(self) -> None:  # pragma: no cover - interfaz
        pass

    @property
    def available(self) -> bool:
        return False

    def snapshot(self) -> MeetingSnapshot:
        return MeetingSnapshot(detail="sin proveedor")


class NullMeetingProvider(MeetingMuteProvider):
    """Sin detección (otros sistemas, o desactivada): siempre UNKNOWN."""

    def __init__(self, reason: str = "detección de Teams no disponible") -> None:
        self._reason = reason

    def snapshot(self) -> MeetingSnapshot:
        return MeetingSnapshot(state=MeetingState.UNKNOWN, detail=self._reason, source="none")


class TeamsUiaProvider(MeetingMuteProvider):
    """Lee el botón de micrófono de Teams por UI Automation en un hilo propio."""

    name = SOURCE_TEAMS_UIA

    def __init__(
        self,
        *,
        poll_ms: int = 250,
        unmuted_expiry_ms: int = 450,
        rescan_s: float = 5.0,
        uia_timeout_ms: int = 1500,
        process_names: tuple[str, ...] = TEAMS_PROCESSES,
        automation_id: str = MIC_AUTOMATION_ID,
    ) -> None:
        self.poll_ms = int(max(100, min(2000, poll_ms)))
        self.unmuted_expiry_ms = int(max(200, min(2000, unmuted_expiry_ms)))
        self.rescan_s = float(max(0.5, rescan_s))
        self.uia_timeout_ms = int(max(200, uia_timeout_ms))
        self.process_names = tuple(n.lower() for n in process_names)
        self.automation_id = automation_id
        # Cualquier lectura (también MUTED) más antigua que esto se considera
        # «proveedor sin respuesta»: cubre un hilo bloqueado en UIA.
        self.stale_ms = max(4 * self.poll_ms, 2000) + self.uia_timeout_ms

        self._lock = threading.Lock()
        self._latest = MeetingSnapshot(detail="sin iniciar", source=self.name)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.reads = 0
        self.rescans = 0
        self.errors = 0

    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._publish(MeetingSnapshot(detail="iniciando", source=self.name))
        self._thread = threading.Thread(target=self._run, name="camvoice-teams-uia", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, self.uia_timeout_ms / 1000.0 + 1.0))
        self._thread = None
        self._publish(MeetingSnapshot(detail="detenido", source=self.name))

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> MeetingSnapshot:
        """Lectura publicada con la caducidad aplicada (nunca UNMUTED viejo)."""
        with self._lock:
            snap = self._latest
        if snap.t_monotonic <= 0:
            return snap
        now = time.monotonic()
        age = snap.age_ms(now)
        if snap.state is MeetingState.UNMUTED and age > self.unmuted_expiry_ms:
            return replace(
                snap,
                state=MeetingState.UNKNOWN,
                detail=f"lectura caducada ({age:.0f} ms > {self.unmuted_expiry_ms} ms)",
            )
        if age > self.stale_ms and snap.state is not MeetingState.UNKNOWN:
            return replace(snap, state=MeetingState.UNKNOWN, detail=f"proveedor sin respuesta ({age / 1000:.1f} s)")
        if not self.running and not self._stop.is_set():
            return replace(snap, state=MeetingState.UNKNOWN, detail="hilo de lectura detenido")
        return snap

    def _publish(self, snap: MeetingSnapshot) -> None:
        with self._lock:
            prev = self._latest
            self._latest = snap
        if prev.state is not snap.state or prev.detail != snap.detail:
            log.info("Teams %s -> %s (%s)", prev.state.value, snap.state.value, snap.detail)

    # ------------------------------------------------------------------ #
    # Hilo de lectura
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        try:
            uia = _Uia(self.uia_timeout_ms)
        except Exception as exc:  # noqa: BLE001 - sin UIA no hay lectura, pero la app sigue
            self._publish(
                MeetingSnapshot(
                    state=MeetingState.UNKNOWN,
                    t_monotonic=time.monotonic(),
                    source=self.name,
                    detail=f"UI Automation no disponible ({type(exc).__name__})",
                )
            )
            log.warning("UI Automation no disponible: %s", exc)
            return
        st = _PollState()
        try:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                try:
                    snap = self._poll(uia, st)
                except Exception as exc:  # noqa: BLE001 - un fallo de UIA no debe matar el hilo
                    self.errors += 1
                    st.drop()
                    snap = MeetingSnapshot(
                        state=MeetingState.UNKNOWN,
                        detail=f"error de lectura ({_err_name(exc)})",
                    )
                read_ms = (time.perf_counter() - t0) * 1000.0
                self._emit(snap, read_ms)
                self._stop.wait(max(0.02, (self.poll_ms - read_ms) / 1000.0))
        finally:
            st.drop()
            uia.close()

    def _emit(self, snap: MeetingSnapshot, read_ms: float) -> None:
        self.reads += 1
        self._publish(replace(snap, t_monotonic=time.monotonic(), source=self.name, read_ms=read_ms))

    def _poll(self, uia: "_Uia", st: "_PollState") -> MeetingSnapshot:
        hwnds = _teams_windows(self.process_names)
        if not hwnds:
            st.drop()
            st.hwnds = frozenset()
            return MeetingSnapshot(state=MeetingState.NO_MEETING, detail="Teams no está visible", windows=0, buttons=0)

        now = time.monotonic()
        current = frozenset(hwnds)
        windows_changed = current != st.hwnds
        st.hwnds = current
        need_rescan = st.cached is None or windows_changed or (now - st.last_scan) >= self.rescan_s

        if st.cached is not None and st.cached.hwnd in current:
            # Lectura rápida del botón ya localizado (propiedad «Current»: UIA
            # la lee en vivo; si el elemento murió, lanza ELEMENTNOTAVAILABLE).
            try:
                label = st.cached.element.name()
            except OSError:
                st.drop()
                need_rescan = True
            else:
                fresh = self._decide(label, len(hwnds), 1)
                if not need_rescan:
                    return fresh
                # La exploración completa puede tardar cientos de ms en un
                # árbol WebView2 grande: se publica antes la lectura fresca
                # para que UNMUTED no caduque por culpa de la propia búsqueda.
                self._emit(fresh, 0.0)
        elif st.cached is not None:
            st.drop()
            need_rescan = True

        # Exploración completa: al principio, si cambió el conjunto de
        # ventanas de Teams (p. ej. una reunión abierta en ventana aparte), si
        # el elemento cacheado dejó de existir (WebView2 reconstruye su árbol)
        # o, como red de seguridad, cada `rescan_s`.
        st.drop()
        st.last_scan = now
        self.rescans += 1
        found: list[_CachedButton] = []
        for hwnd in hwnds:
            root = uia.element_from_handle(hwnd)
            if root is None:
                continue
            try:
                for element in root.find_all(uia.mic_condition):
                    found.append(_CachedButton(hwnd, element))
            finally:
                root.release()

        if not found:
            return MeetingSnapshot(
                state=MeetingState.NO_MEETING,
                detail=f"sin control de micrófono en {len(hwnds)} ventana(s) de Teams",
                windows=len(hwnds),
                buttons=0,
            )
        if len(found) > 1:
            # Un mismo documento puede tener copias fuera de pantalla (diseño
            # adaptable). Si queda exactamente una visible, es la reunión.
            visible = [b for b in found if not b.element.is_offscreen()]
            if len(visible) == 1:
                for b in found:
                    if b is not visible[0]:
                        b.release()
                found = visible
            else:
                for b in found:
                    b.release()
                return MeetingSnapshot(
                    state=MeetingState.UNKNOWN,
                    detail=f"{len(found)} controles de micrófono: varias reuniones o ventanas",
                    windows=len(hwnds),
                    buttons=len(found),
                )
        st.cached = found[0]
        label = st.cached.element.name()
        return self._decide(label, len(hwnds), 1)

    @staticmethod
    def _decide(label: str, windows: int, buttons: int) -> MeetingSnapshot:
        state = classify_mic_label(label)
        detail = "" if state is not MeetingState.UNKNOWN else "etiqueta del botón no reconocida"
        return MeetingSnapshot(state=state, detail=detail, label=label[:60], windows=windows, buttons=buttons)


def default_provider(*, poll_ms: int = 250, unmuted_expiry_ms: int = 450) -> MeetingMuteProvider:
    """Proveedor UIA en Windows; nulo en otros sistemas o con
    `CAMVOICE_NO_TEAMS_UIA=1` (pruebas, equipos sin Teams)."""
    if sys.platform != "win32":
        return NullMeetingProvider("la lectura de Teams solo existe en Windows")
    if os.environ.get("CAMVOICE_NO_TEAMS_UIA", "").strip() not in ("", "0", "false", "no"):
        return NullMeetingProvider("detección de Teams desactivada (CAMVOICE_NO_TEAMS_UIA)")
    return TeamsUiaProvider(poll_ms=poll_ms, unmuted_expiry_ms=unmuted_expiry_ms)


def _err_name(exc: BaseException) -> str:
    code = getattr(exc, "winerror", None)
    if isinstance(code, int):
        return f"{type(exc).__name__} 0x{code & 0xFFFFFFFF:08X}"
    return type(exc).__name__


# --------------------------------------------------------------------------- #
# Win32 / COM mínimo (solo Windows; se importa perezosamente)
# --------------------------------------------------------------------------- #

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    _oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_str(cls, text: str) -> "_GUID":
            guid = cls()
            _ole32.CLSIDFromString(ctypes.c_wchar_p("{" + text.strip("{}") + "}"), ctypes.byref(guid))
            return guid

    _ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_GUID)]
    _ole32.CLSIDFromString.restype = ctypes.HRESULT
    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = ctypes.HRESULT
    _ole32.CoUninitialize.argtypes = []
    _ole32.CoUninitialize.restype = None
    _ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _ole32.CoCreateInstance.restype = ctypes.HRESULT
    _oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
    _oleaut32.SysAllocString.restype = ctypes.c_void_p
    _oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
    _oleaut32.SysFreeString.restype = None

    _CLSID_CUIAutomation8 = "E22AD333-B25F-460C-83D0-0581107395C9"
    _CLSID_CUIAutomation = "FF48DBA4-60EF-4201-AA87-54103EEF594E"
    _IID_IUIAutomation = "30CBE57D-D9D0-452A-AB13-7AC5AC4825EE"
    _IID_IUIAutomation2 = "34723AFF-0C9D-49D0-9896-7AB52DF8CD8A"

    _COINIT_MULTITHREADED = 0x0
    _CLSCTX_INPROC_SERVER = 0x1
    _RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
    _S_FALSE = 1

    _VT_BSTR = 8
    _TreeScope_Descendants = 0x4
    _UIA_AutomationIdPropertyId = 30011

    # Índices en la tabla virtual (IUnknown ocupa 0-2).
    _IUnknown_Release = 2
    _IUIAutomation_ElementFromHandle = 6
    _IUIAutomation_CreatePropertyCondition = 23
    _IUIAutomation2_put_ConnectionTimeout = 61
    _IUIAutomation2_put_TransactionTimeout = 63
    _Element_FindAll = 6
    _Element_get_CurrentName = 23
    _Element_get_CurrentAutomationId = 29
    _Element_get_CurrentIsOffscreen = 38
    _Array_get_Length = 3
    _Array_GetElement = 4

    class _VariantUnion(ctypes.Union):
        _fields_ = [
            ("lVal", ctypes.c_long),
            ("llVal", ctypes.c_longlong),
            ("ptr", ctypes.c_void_p),
            ("_pad", ctypes.c_byte * 16),
        ]

    class _VARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
            ("wReserved2", ctypes.c_ushort),
            ("wReserved3", ctypes.c_ushort),
            ("value", _VariantUnion),
        ]

    def _vcall(obj: int, index: int, argtypes: tuple, *args):  # noqa: ANN001, ANN202
        """Llama al método `index` de la tabla virtual COM de `obj` (HRESULT)."""
        vtable = ctypes.cast(ctypes.c_void_p(obj), ctypes.POINTER(ctypes.c_void_p))[0]
        fn_ptr = ctypes.cast(ctypes.c_void_p(vtable), ctypes.POINTER(ctypes.c_void_p))[index]
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        return proto(fn_ptr)(obj, *args)

    def _release(obj: int | None) -> None:
        if obj:
            try:
                proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
                vtable = ctypes.cast(ctypes.c_void_p(obj), ctypes.POINTER(ctypes.c_void_p))[0]
                fn_ptr = ctypes.cast(ctypes.c_void_p(vtable), ctypes.POINTER(ctypes.c_void_p))[_IUnknown_Release]
                proto(fn_ptr)(obj)
            except Exception:  # pragma: no cover - liberar nunca debe fallar hacia fuera
                pass

    def _read_bstr(obj: int, index: int) -> str:
        out = ctypes.c_void_p()
        _vcall(obj, index, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(out))
        if not out.value:
            return ""
        try:
            return ctypes.wstring_at(out.value)
        finally:
            _oleaut32.SysFreeString(out)

    class _Element:
        """IUIAutomationElement (propiedad de quien lo crea; `release()` al acabar)."""

        __slots__ = ("ptr",)

        def __init__(self, ptr: int) -> None:
            self.ptr = ptr

        def name(self) -> str:
            return _read_bstr(self.ptr, _Element_get_CurrentName)

        def automation_id(self) -> str:
            return _read_bstr(self.ptr, _Element_get_CurrentAutomationId)

        def is_offscreen(self) -> bool:
            out = ctypes.c_int(0)
            try:
                _vcall(self.ptr, _Element_get_CurrentIsOffscreen, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(out))
            except OSError:
                return False
            return bool(out.value)

        def find_all(self, condition: int) -> list["_Element"]:
            arr = ctypes.c_void_p()
            _vcall(
                self.ptr,
                _Element_FindAll,
                (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                _TreeScope_Descendants,
                condition,
                ctypes.byref(arr),
            )
            if not arr.value:
                return []
            out: list[_Element] = []
            try:
                n = ctypes.c_int(0)
                _vcall(arr.value, _Array_get_Length, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(n))
                for i in range(n.value):
                    el = ctypes.c_void_p()
                    _vcall(
                        arr.value,
                        _Array_GetElement,
                        (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                        i,
                        ctypes.byref(el),
                    )
                    if el.value:
                        out.append(_Element(el.value))
            finally:
                _release(arr.value)
            return out

        def release(self) -> None:
            _release(self.ptr)
            self.ptr = 0

    class _CachedButton:
        __slots__ = ("hwnd", "element")

        def __init__(self, hwnd: int, element: _Element) -> None:
            self.hwnd = hwnd
            self.element = element

        def release(self) -> None:
            self.element.release()

    class _PollState:
        """Estado del bucle de lectura: botón cacheado y ventanas vistas."""

        __slots__ = ("cached", "hwnds", "last_scan")

        def __init__(self) -> None:
            self.cached: _CachedButton | None = None
            self.hwnds: frozenset[int] = frozenset()
            self.last_scan = 0.0

        def drop(self) -> None:
            if self.cached is not None:
                self.cached.release()
                self.cached = None

    class _Uia:
        """Cliente IUIAutomation del hilo actual (COM MTA)."""

        def __init__(self, timeout_ms: int) -> None:
            self._coinit = False
            hr = _ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
            # S_OK / S_FALSE: inicializado (o ya lo estaba). RPC_E_CHANGED_MODE:
            # el hilo ya tenía otro apartamento; se usa igualmente.
            self._coinit = hr in (0, _S_FALSE)
            self.ptr = 0
            ptr = ctypes.c_void_p()
            try:
                _ole32.CoCreateInstance(
                    ctypes.byref(_GUID.from_str(_CLSID_CUIAutomation8)),
                    None,
                    _CLSCTX_INPROC_SERVER,
                    ctypes.byref(_GUID.from_str(_IID_IUIAutomation2)),
                    ctypes.byref(ptr),
                )
                self.ptr = ptr.value or 0
                self.version = 2
                # Tiempos de espera: un Teams colgado no debe bloquear la lectura.
                for index in (_IUIAutomation2_put_ConnectionTimeout, _IUIAutomation2_put_TransactionTimeout):
                    try:
                        _vcall(self.ptr, index, (ctypes.c_int,), int(timeout_ms))
                    except OSError:
                        pass
            except OSError:
                _ole32.CoCreateInstance(
                    ctypes.byref(_GUID.from_str(_CLSID_CUIAutomation)),
                    None,
                    _CLSCTX_INPROC_SERVER,
                    ctypes.byref(_GUID.from_str(_IID_IUIAutomation)),
                    ctypes.byref(ptr),
                )
                self.ptr = ptr.value or 0
                self.version = 1
            if not self.ptr:
                raise OSError("CoCreateInstance(CUIAutomation) devolvió un puntero nulo")
            self.mic_condition = self._property_condition(_UIA_AutomationIdPropertyId, MIC_AUTOMATION_ID)

        def _property_condition(self, prop: int, value: str) -> int:
            var = _VARIANT()
            var.vt = _VT_BSTR
            bstr = _oleaut32.SysAllocString(value)
            var.value.ptr = bstr
            cond = ctypes.c_void_p()
            try:
                _vcall(
                    self.ptr,
                    _IUIAutomation_CreatePropertyCondition,
                    (ctypes.c_int, _VARIANT, ctypes.POINTER(ctypes.c_void_p)),
                    prop,
                    var,
                    ctypes.byref(cond),
                )
            finally:
                _oleaut32.SysFreeString(bstr)
            if not cond.value:
                raise OSError("CreatePropertyCondition devolvió un puntero nulo")
            return cond.value

        def element_from_handle(self, hwnd: int) -> _Element | None:
            out = ctypes.c_void_p()
            try:
                _vcall(
                    self.ptr,
                    _IUIAutomation_ElementFromHandle,
                    (wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)),
                    hwnd,
                    ctypes.byref(out),
                )
            except OSError:
                return None
            return _Element(out.value) if out.value else None

        def close(self) -> None:
            _release(getattr(self, "mic_condition", 0))
            _release(self.ptr)
            self.ptr = 0
            if self._coinit:
                _ole32.CoUninitialize()
                self._coinit = False

    # --- ventanas de Teams -------------------------------------------- #

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    _process_names: dict[int, str] = {}

    def _process_image(pid: int) -> str:
        """Nombre del ejecutable (minúsculas) del proceso; '' si no se puede leer."""
        cached = _process_names.get(pid)
        if cached is not None:
            return cached
        name = ""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value).lower()
            finally:
                _kernel32.CloseHandle(handle)
        if len(_process_names) > 512:
            _process_names.clear()
        _process_names[pid] = name
        return name

    def _teams_windows(process_names: tuple[str, ...]) -> list[int]:
        """HWND de las ventanas visibles de nivel superior de Teams."""
        found: list[int] = []
        own_pid = os.getpid()
        seen_pids: dict[int, bool] = {}

        @_WNDENUMPROC
        def _cb(hwnd, _lparam):  # noqa: ANN001, ANN202
            try:
                if not _user32.IsWindowVisible(hwnd):
                    return True
                pid = wintypes.DWORD(0)
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                p = int(pid.value)
                if not p or p == own_pid:
                    return True
                is_teams = seen_pids.get(p)
                if is_teams is None:
                    is_teams = _process_image(p) in process_names
                    seen_pids[p] = is_teams
                if is_teams:
                    found.append(int(hwnd))
            except Exception:  # pragma: no cover - nunca interrumpir la enumeración
                pass
            return True

        _user32.EnumWindows(_cb, 0)
        # Los PID se reutilizan: no conservar nombres de procesos ya cerrados.
        for pid in [p for p in _process_names if p not in seen_pids]:
            _process_names.pop(pid, None)
        return found

else:  # pragma: no cover - otros sistemas

    class _CachedButton:  # type: ignore[no-redef]
        pass

    class _PollState:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.cached = None
            self.hwnds: frozenset[int] = frozenset()
            self.last_scan = 0.0

        def drop(self) -> None:
            self.cached = None

    class _Uia:  # type: ignore[no-redef]
        def __init__(self, timeout_ms: int) -> None:
            raise OSError("UI Automation solo existe en Windows")

        def close(self) -> None:
            pass

    def _teams_windows(process_names: tuple[str, ...]) -> list[int]:  # type: ignore[misc]
        return []
