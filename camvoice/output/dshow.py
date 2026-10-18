"""Lo que ve una aplicación, no lo que hay escrito en el registro.

`filters.list_directshow_cameras` lee claves. Que existan no demuestra que
Teams vea la cámara: la clave puede apuntar a otra arquitectura, estar en una
colmena que el proceso no mira (los procesos elevados ignoran HKCU\\Classes) o
tener datos que el enumerador rechace. Este módulo pregunta a DirectShow igual
que lo hace una aplicación de videollamada:

- `enumerate_video_inputs` usa `ICreateDevEnum`, el enumerador que recorren
  Zoom, Discord, el cliente clásico de Teams o Chrome con DirectShow.
- `roundtrip` monta un grafo de captura (cámara -> SampleGrabber ->
  NullRenderer), envía fotogramas por `pyvirtualcam` y comprueba que llegan.

No se usa OpenCV como consumidor: su backend DirectShow no abre el filtro
UnityCapture en ninguna variante probada (tampoco depende de dónde esté
registrado), mientras que un grafo DirectShow normal y ffmpeg sí lo abren.
Ver `docs/VALIDACION_INSTALACION_USUARIO.md`.

Solo usa `ctypes` sobre `ole32`; no requiere `comtypes`.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

__all__ = [
    "DirectShowDevice",
    "enumerate_video_inputs",
    "enumerate_video_inputs_x86",
    "RoundtripResult",
    "roundtrip",
    "DShowError",
]


class DShowError(RuntimeError):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        guid = cls()
        hr = ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
        if hr:
            raise DShowError(f"GUID inválido: {text}")
        return guid


class _AM_MEDIA_TYPE(ctypes.Structure):
    _fields_ = [
        ("majortype", _GUID),
        ("subtype", _GUID),
        ("bFixedSizeSamples", wintypes.BOOL),
        ("bTemporalCompression", wintypes.BOOL),
        ("lSampleSize", wintypes.ULONG),
        ("formattype", _GUID),
        ("pUnk", ctypes.c_void_p),
        ("cbFormat", wintypes.ULONG),
        ("pbFormat", ctypes.c_void_p),
    ]


# Identificadores de strmif.h / uuids.h. Los IID de DirectShow terminan en
# 0020AF0BA770 (comprobado contra la biblioteca de tipos de qedit.dll).
_CLSID_SystemDeviceEnum = "{62BE5D10-60EB-11D0-BD3B-00A0C911CE86}"
_IID_ICreateDevEnum = "{29840822-5B84-11D0-BD3B-00A0C911CE86}"
_IID_IPropertyBag = "{55272A00-42CB-11CE-8135-00AA004BB851}"
_CLSID_VideoInputDeviceCategory = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
_CLSID_FilterGraph = "{E436EBB3-524F-11CE-9F53-0020AF0BA770}"
_IID_IGraphBuilder = "{56A868A9-0AD4-11CE-B03A-0020AF0BA770}"
_IID_IBaseFilter = "{56A86895-0AD4-11CE-B03A-0020AF0BA770}"
_IID_IMediaControl = "{56A868B1-0AD4-11CE-B03A-0020AF0BA770}"
_CLSID_CaptureGraphBuilder2 = "{BF87B6E1-8C27-11D0-B3F0-00AA003761C5}"
_IID_ICaptureGraphBuilder2 = "{93E5A4E0-2D50-11D2-ABFA-00A0C9C6E38D}"
_CLSID_SampleGrabber = "{C1F400A0-3F08-11D3-9F0B-006008039E37}"
_IID_ISampleGrabber = "{6B652FFF-11FE-4FCE-92AD-0266B5D7C78F}"
_CLSID_NullRenderer = "{C1F400A4-3F08-11D3-9F0B-006008039E37}"
_PIN_CATEGORY_CAPTURE = "{FB6C4281-0353-11D1-905F-0000C0CC16BA}"
_MEDIATYPE_Video = "{73646976-0000-0010-8000-00AA00389B71}"
_MEDIASUBTYPE_RGB24 = "{E436EB7D-524F-11CE-9F53-0020AF0BA770}"

_CLSCTX_INPROC_SERVER = 1
_VT_BSTR = 8
_RPC_E_CHANGED_MODE = -2147417850
_VFW_E_WRONG_STATE = -2147220953


@dataclass(frozen=True, slots=True)
class DirectShowDevice:
    index: int
    """Posición en la enumeración de DirectShow."""
    friendly_name: str
    clsid: str
    """CLSID del filtro; en dispositivos físicos es el de KsProxy."""


def _method(ptr: ctypes.c_void_p, index: int, restype, *argtypes):  # noqa: ANN001, ANN202
    """Función de la vtable de un objeto COM, ya ligada a `ptr`."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    fn = prototype(vtable[index])
    return lambda *args: fn(ptr, *args)


def _release(ptr: ctypes.c_void_p) -> None:
    if ptr:
        _method(ptr, 2, wintypes.ULONG)()


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise DShowError(f"{what} falló: 0x{hr & 0xFFFFFFFF:08X}")


def _create(clsid: str, iid: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    hr = ctypes.windll.ole32.CoCreateInstance(
        ctypes.byref(_GUID.parse(clsid)), None, _CLSCTX_INPROC_SERVER,
        ctypes.byref(_GUID.parse(iid)), ctypes.byref(out),
    )
    _check(hr, f"CoCreateInstance {clsid}")
    return out


def _query(ptr: ctypes.c_void_p, iid: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    qi = _method(ptr, 0, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
    _check(qi(ctypes.byref(_GUID.parse(iid)), ctypes.byref(out)), f"QueryInterface {iid}")
    return out


class _Com:
    """CoInitialize/CoUninitialize emparejados; tolera un apartamento previo."""

    def __enter__(self) -> "_Com":
        hr = ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        if hr not in (0, 1, _RPC_E_CHANGED_MODE):
            _check(hr, "CoInitializeEx")
        self._uninit = hr in (0, 1)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._uninit:
            ctypes.windll.ole32.CoUninitialize()


def _read_property(bag: ctypes.c_void_p, name: str) -> str:
    variant = (ctypes.c_ubyte * 24)()  # VARIANT: 16 bytes en x86, 24 en x64
    read = _method(bag, 3, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p)
    if read(name, variant, None) != 0:
        return ""
    try:
        vt = int.from_bytes(bytes(variant[0:2]), "little")
        if vt != _VT_BSTR:
            return ""
        bstr = ctypes.c_void_p.from_buffer(variant, 8).value
        return ctypes.wstring_at(bstr) if bstr else ""
    finally:
        ctypes.windll.oleaut32.VariantClear(variant)


def _walk(visit) -> None:  # noqa: ANN001
    """Llama a `visit(index, moniker, nombre, clsid)` por cada dispositivo.

    Si `visit` devuelve True se detiene. El moniker solo es válido durante la
    llamada.
    """
    dev_enum = _create(_CLSID_SystemDeviceEnum, _IID_ICreateDevEnum)
    enum_moniker = ctypes.c_void_p()
    try:
        create = _method(dev_enum, 3, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD)
        hr = create(ctypes.byref(_GUID.parse(_CLSID_VideoInputDeviceCategory)),
                    ctypes.byref(enum_moniker), 0)
        if hr == 1 or not enum_moniker:  # S_FALSE: categoría vacía
            return
        _check(hr, "CreateClassEnumerator")
        next_ = _method(enum_moniker, 3, ctypes.c_long, wintypes.ULONG, ctypes.c_void_p, ctypes.c_void_p)
        iid_bag = _GUID.parse(_IID_IPropertyBag)
        index = 0
        while True:
            moniker = ctypes.c_void_p()
            fetched = wintypes.ULONG()
            if next_(1, ctypes.byref(moniker), ctypes.byref(fetched)) != 0 or not fetched.value:
                break
            bag = ctypes.c_void_p()
            try:
                bind = _method(moniker, 9, ctypes.c_long,  # IMoniker::BindToStorage
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
                name = clsid = ""
                if bind(None, None, ctypes.byref(iid_bag), ctypes.byref(bag)) == 0:
                    name = _read_property(bag, "FriendlyName")
                    clsid = _read_property(bag, "CLSID").upper()
                _release(bag)
                bag = ctypes.c_void_p()
                if visit(index, moniker, name, clsid):
                    return
            finally:
                _release(bag)
                _release(moniker)
            index += 1
    finally:
        _release(enum_moniker)
        _release(dev_enum)


def enumerate_video_inputs() -> list[DirectShowDevice]:
    """Dispositivos de captura de video tal como los ve DirectShow en este
    proceso (su arquitectura, su usuario, su nivel de integridad)."""
    if sys.platform != "win32":
        return []
    devices: list[DirectShowDevice] = []
    with _Com():
        _walk(lambda i, _m, name, clsid: devices.append(DirectShowDevice(i, name, clsid)))
    return devices


_X86_ENUM_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
[ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ICvCreateDevEnum { [PreserveSig] int CreateClassEnumerator([In] ref Guid cat, out IEnumMoniker e, int flags); }
[ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ICvPropertyBag { [PreserveSig] int Read([MarshalAs(UnmanagedType.LPWStr)] string name, ref object val, IntPtr log); }
public static class CvEnum {
  public static string[] List() {
    var de = (ICvCreateDevEnum)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")));
    var cat = new Guid("860BB310-5D01-11D0-BD3B-00A0C911CE86");
    var list = new List<string>();
    IEnumMoniker em;
    if (de.CreateClassEnumerator(ref cat, out em, 0) != 0 || em == null) return list.ToArray();
    var m = new IMoniker[1];
    while (em.Next(1, m, IntPtr.Zero) == 0) {
      object bagObj; var iid = typeof(ICvPropertyBag).GUID;
      m[0].BindToStorage(null, null, ref iid, out bagObj);
      var bag = (ICvPropertyBag)bagObj; object name = null, clsid = null;
      bag.Read("FriendlyName", ref name, IntPtr.Zero); bag.Read("CLSID", ref clsid, IntPtr.Zero);
      list.Add(name + "|" + clsid);
    }
    return list.ToArray();
  }
}
'@
"PROCESO64=" + [Environment]::Is64BitProcess
[CvEnum]::List()
"""


def enumerate_video_inputs_x86(timeout: float = 60.0) -> list[DirectShowDevice]:
    """Enumeración DirectShow hecha desde un proceso de **32 bits**.

    Python suele ser de 64 bits; para ver lo que ve una aplicación de 32 bits
    se usa el PowerShell de SysWOW64 con interop de .NET. Lanza `DShowError`
    si no se puede (sin SysWOW64, PowerShell en modo de lenguaje restringido,
    Add-Type bloqueado por directiva): eso es «No verificable», no un fallo.
    """
    import base64
    import os
    import subprocess

    if sys.platform != "win32":
        raise DShowError("Solo existe en Windows.")
    shell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                         "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(shell):
        raise DShowError(f"No hay PowerShell de 32 bits ({shell}).")
    encoded = base64.b64encode(_X86_ENUM_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        done = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DShowError(f"No se pudo ejecutar PowerShell de 32 bits: {exc}") from exc
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or "PROCESO64=False" not in lines:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise DShowError("PowerShell de 32 bits no pudo enumerar: " + (detail[0] if detail else "sin salida"))
    devices: list[DirectShowDevice] = []
    for line in lines[lines.index("PROCESO64=False") + 1:]:
        name, _, clsid = line.partition("|")
        devices.append(DirectShowDevice(len(devices), name, clsid.upper()))
    return devices


@dataclass(slots=True)
class RoundtripResult:
    device: DirectShowDevice | None
    opened: bool = False
    """El grafo de captura se montó y arrancó."""
    width: int = 0
    height: int = 0
    samples: int = 0
    """Lecturas del búfer con un fotograma."""
    samples_matching: int = 0
    """Lecturas cuyo píxel central es uno de los colores enviados."""
    color_changes: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.opened and self.samples_matching >= 5 and self.color_changes >= 2


_COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
"""RGB que se envían en ciclo; se busca cada uno en lo que llega al grafo."""


def _closest(pixel) -> int | None:  # noqa: ANN001
    """Índice del color enviado más parecido a `pixel` (RGB), si está cerca."""
    best, best_d = None, 1e9
    for i, color in enumerate(_COLORS):
        d = sum((int(a) - b) ** 2 for a, b in zip(pixel, color))
        if d < best_d:
            best, best_d = i, d
    return best if best_d < 3 * 60 ** 2 else None


def _bind_filter(camera_name: str, result: RoundtripResult) -> ctypes.c_void_p:
    found = ctypes.c_void_p()

    def visit(index: int, moniker: ctypes.c_void_p, name: str, clsid: str) -> bool:
        if name != camera_name:
            return False
        result.device = DirectShowDevice(index, name, clsid)
        bind = _method(moniker, 8, ctypes.c_long,  # IMoniker::BindToObject
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        _check(bind(None, None, ctypes.byref(_GUID.parse(_IID_IBaseFilter)), ctypes.byref(found)),
               "BindToObject (cargar el filtro)")
        return True

    _walk(visit)
    return found


def _capture(camera_name: str, result: RoundtripResult, seconds: float) -> None:
    """Monta cámara -> SampleGrabber(RGB24) -> NullRenderer y muestrea."""
    objs: list[ctypes.c_void_p] = []
    control = ctypes.c_void_p()
    try:
        source = _bind_filter(camera_name, result)
        if not source:
            result.error = f"DirectShow no enumera ningún dispositivo llamado «{camera_name}»."
            return
        objs.append(source)
        graph = _create(_CLSID_FilterGraph, _IID_IGraphBuilder)
        objs.append(graph)
        builder = _create(_CLSID_CaptureGraphBuilder2, _IID_ICaptureGraphBuilder2)
        objs.append(builder)
        try:
            grabber_filter = _create(_CLSID_SampleGrabber, _IID_IBaseFilter)
        except DShowError:
            result.error = "No verificable: este Windows no tiene SampleGrabber (qedit.dll)."
            return
        objs.append(grabber_filter)
        grabber = _query(grabber_filter, _IID_ISampleGrabber)
        objs.append(grabber)
        null = _create(_CLSID_NullRenderer, _IID_IBaseFilter)
        objs.append(null)

        mt = _AM_MEDIA_TYPE()
        mt.majortype = _GUID.parse(_MEDIATYPE_Video)
        mt.subtype = _GUID.parse(_MEDIASUBTYPE_RGB24)
        _check(_method(grabber, 4, ctypes.c_long, ctypes.c_void_p)(ctypes.byref(mt)), "SetMediaType")
        _check(_method(grabber, 6, ctypes.c_long, wintypes.BOOL)(True), "SetBufferSamples")

        add = _method(graph, 3, ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)
        _check(add(source, "camara"), "AddFilter (cámara)")
        _check(add(grabber_filter, "grabber"), "AddFilter (grabber)")
        _check(add(null, "null"), "AddFilter (null)")
        _check(_method(builder, 3, ctypes.c_long, ctypes.c_void_p)(graph), "SetFiltergraph")
        render = _method(builder, 7, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        _check(render(ctypes.byref(_GUID.parse(_PIN_CATEGORY_CAPTURE)),
                      ctypes.byref(_GUID.parse(_MEDIATYPE_Video)), source, grabber_filter, null),
               "RenderStream (conectar la cámara)")

        connected = _AM_MEDIA_TYPE()
        _check(_method(grabber, 5, ctypes.c_long, ctypes.c_void_p)(ctypes.byref(connected)),
               "GetConnectedMediaType")
        if connected.pbFormat and connected.cbFormat >= 88:
            header = ctypes.string_at(connected.pbFormat, 88)  # VIDEOINFOHEADER
            result.width = int.from_bytes(header[52:56], "little", signed=True)
            result.height = abs(int.from_bytes(header[56:60], "little", signed=True))
        if connected.pbFormat:
            ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(connected.pbFormat))
        if connected.pUnk:
            _release(ctypes.c_void_p(connected.pUnk))
        if result.width <= 0 or result.height <= 0:
            result.error = "El formato conectado no es un VIDEOINFOHEADER con tamaño."
            return

        control = _query(graph, _IID_IMediaControl)
        _check(_method(control, 7, ctypes.c_long)(), "IMediaControl::Run")
        result.opened = True

        get_buffer = _method(grabber, 7, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
        stride = (result.width * 3 + 3) & ~3
        size = wintypes.LONG(stride * result.height)
        buffer = (ctypes.c_ubyte * size.value)()
        last: int | None = None
        started = time.monotonic()
        while time.monotonic() - started < seconds:
            time.sleep(0.05)
            size.value = len(buffer)
            hr = get_buffer(ctypes.byref(size), buffer)
            if hr == _VFW_E_WRONG_STATE or hr < 0:
                continue  # todavía no ha llegado ningún fotograma
            result.samples += 1
            offset = (result.height // 2) * stride + (result.width // 2) * 3
            b, g, r = buffer[offset], buffer[offset + 1], buffer[offset + 2]
            match = _closest((r, g, b))
            if match is not None:
                result.samples_matching += 1
                if last is not None and match != last:
                    result.color_changes += 1
                last = match
    finally:
        if control:
            _method(control, 9, ctypes.c_long)()  # Stop
            _release(control)
        for obj in reversed(objs):
            _release(obj)


def roundtrip(camera_name: str, *, seconds: float = 4.0, width: int = 640,
              height: int = 360, fps: int = 20) -> RoundtripResult:
    """Envía fotogramas a la cámara y los recibe como lo haría una aplicación.

    Pasa si el grafo DirectShow carga el filtro con ese nombre, recibe al
    menos cinco muestras con los colores enviados y ve el color cambiar.
    """
    result = RoundtripResult(None)
    if sys.platform != "win32":
        result.error = "Solo existe en Windows."
        return result
    try:
        import numpy as np
        import pyvirtualcam
    except ImportError as exc:
        result.error = f"Falta una dependencia para la prueba: {exc.name}"
        return result

    stop = threading.Event()
    ready = threading.Event()
    sender_error: list[str] = []

    def send() -> None:
        try:
            with pyvirtualcam.Camera(width, height, fps, backend="unitycapture",
                                     device=camera_name, print_fps=False) as cam:
                frame = np.zeros((height, width, 3), np.uint8)
                started = time.monotonic()
                ready.set()
                while not stop.is_set():
                    frame[:] = _COLORS[int((time.monotonic() - started) / 0.5) % len(_COLORS)]
                    cam.send(frame)
                    cam.sleep_until_next_frame()
        except Exception as exc:  # noqa: BLE001 - se informa en el resultado
            sender_error.append(f"{type(exc).__name__}: {exc}")
        finally:
            ready.set()

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    ready.wait(timeout=5)
    try:
        if sender_error:
            result.error = "pyvirtualcam: " + sender_error[0]
            return result
        with _Com():
            _capture(camera_name, result, seconds)
    except DShowError as exc:
        result.error = str(exc)
    finally:
        stop.set()
        sender.join(timeout=3)
    if sender_error and not result.error:
        result.error = "pyvirtualcam: " + sender_error[0]
    elif not result.ok and not result.error:
        result.error = (
            f"Se tomaron {result.samples} muestras, {result.samples_matching} con los "
            f"colores enviados y {result.color_changes} cambios de color."
        )
    return result
