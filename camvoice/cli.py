"""Interfaz de línea de comandos.

Existe para que todo sea verificable sin abrir la ventana: diagnóstico del
entorno, preparación de clips, prueba del detector con el micrófono real y
medición de rendimiento. Es el camino que usan los criterios de aceptación.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from .config import AppConfig, cache_dir, config_path, frame_bytes
from .output.filters import DEFAULT_CAMERA_NAME

__all__ = ["main", "build_parser"]


# --------------------------------------------------------------------------- #
# Utilidades de presentación
# --------------------------------------------------------------------------- #

OK = "[ok]"
FAIL = "[--]"
WARN = "[!!]"


def _bar(value_db: float, floor: float = -60.0, width: int = 24) -> str:
    """Barra de nivel en texto, de `floor` dBFS a 0."""
    norm = (max(floor, min(0.0, value_db)) - floor) / -floor
    filled = int(round(norm * width))
    return "#" * filled + "." * (width - filled)


def _apply_overrides(cfg: AppConfig, args: argparse.Namespace) -> None:
    v, a, o, m = cfg.video, cfg.audio, cfg.output, cfg.metrics
    if getattr(args, "speaking", None):
        v.speaking_clip = str(Path(args.speaking).expanduser().resolve())
    if getattr(args, "silent", None):
        v.silent_clip = str(Path(args.silent).expanduser().resolve())
    if getattr(args, "width", None):
        v.width = args.width
    if getattr(args, "height", None):
        v.height = args.height
    if getattr(args, "fps", None):
        v.fps = args.fps
    if getattr(args, "fit", None):
        v.fit = args.fit
    if getattr(args, "mirror", None) is not None:
        v.mirror = args.mirror
    if getattr(args, "transition", None) is not None:
        v.transition_ms = args.transition
    if getattr(args, "loop_mode", None):
        v.loop_mode = args.loop_mode
    if getattr(args, "pixel_format", None):
        v.pixel_format = args.pixel_format
    if getattr(args, "playback", None):
        v.playback = args.playback
    if getattr(args, "seed", None) is not None:
        v.seed = args.seed
    if getattr(args, "manifest", None):
        v.library_manifest = str(Path(args.manifest).expanduser().resolve())
    if getattr(args, "no_normalize", False):
        v.normalize_clips = False
    if getattr(args, "mic", None) is not None:
        a.device = args.mic
        a.device_name = ""
    if getattr(args, "vad_backend", None):
        cfg.vad.backend = args.vad_backend
    if getattr(args, "aggressiveness", None) is not None:
        cfg.vad.aggressiveness = args.aggressiveness
    if getattr(args, "attack", None) is not None:
        cfg.vad.attack_ms = args.attack
    if getattr(args, "release", None) is not None:
        cfg.vad.release_ms = args.release
    if getattr(args, "no_virtual_cam", False):
        o.enabled = False
    if getattr(args, "backend", None):
        o.backend = args.backend
    for campo in (
        "blur", "pixelate", "brightness", "contrast",
        "saturation", "vignette", "grain", "zoom",
    ):
        valor = getattr(args, campo, None)
        if valor is not None:
            setattr(cfg.effects, campo, float(valor))
    if getattr(args, "no_effects", False):
        cfg.effects.enabled = False
    if getattr(args, "metrics", False):
        m.enabled = True
    if getattr(args, "metrics_csv", None):
        m.enabled = True
        m.csv_path = str(Path(args.metrics_csv).expanduser())


def _load(args: argparse.Namespace) -> AppConfig:
    path = Path(args.config) if getattr(args, "config", None) else None
    cfg = AppConfig.load(path)
    _apply_overrides(cfg, args)
    if getattr(args, "save", False):
        saved = cfg.save(path)
        print(f"Configuración guardada en {saved}")
    return cfg


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #


def cmd_devices(args: argparse.Namespace) -> int:
    from .audio.capture import AudioError, list_input_devices

    try:
        devices = list_input_devices()
    except AudioError as exc:
        print(f"{FAIL} {exc}")
        return 1
    if not devices:
        print(f"{FAIL} No se encontró ningún dispositivo de entrada de audio.")
        return 1
    print("Dispositivos de entrada disponibles (los de WASAPI primero):\n")
    for dev in devices:
        print(f"  {dev.label}")
        print(f"       canales: {dev.channels}   nativo: {dev.default_samplerate:.0f} Hz")
    print(f"\nÚsalo con:  camvoice run --mic {devices[0].index}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _load(args)
    problems = 0

    print("== Entorno ==")
    print(f"  Python            {sys.version.split()[0]} ({sys.executable})")
    for module, label in (
        ("numpy", "NumPy"),
        ("cv2", "OpenCV"),
        ("sounddevice", "sounddevice"),
        ("pyvirtualcam", "pyvirtualcam"),
        ("psutil", "psutil"),
    ):
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", "?")
            print(f"  {OK} {label:<16}{version}")
        except Exception as exc:
            print(f"  {FAIL} {label:<16}no disponible ({exc})")
            problems += 1

    from .audio.vad import webrtc_available

    if webrtc_available():
        print(f"  {OK} WebRTC VAD      disponible")
    else:
        print(f"  {WARN} WebRTC VAD      no disponible; se usará el detector RMS")

    print("\n== Audio ==")
    from .audio.capture import AudioError, list_input_devices

    try:
        devices = list_input_devices()
        if devices:
            print(f"  {OK} {len(devices)} dispositivos de entrada")
            print(f"     por defecto: {devices[0].label}")
        else:
            print(f"  {FAIL} Sin dispositivos de entrada")
            problems += 1
    except AudioError as exc:
        print(f"  {FAIL} {exc}")
        problems += 1

    print("\n== Cámara virtual ==")
    from .output.filters import describe_availability, user_install_status
    from .output.sink import diagnose_virtual_camera

    for line in describe_availability().splitlines():
        print(f"  {line}")
    for arch, status in user_install_status().items():
        if status.state == "roto":
            print(f"  {FAIL} {arch}: " + "; ".join(status.problems))
            problems += 1
    diag = diagnose_virtual_camera(
        cfg.output, width=cfg.video.width, height=cfg.video.height, fps=cfg.video.fps
    )
    if diag.available:
        print(f"  {OK} {diag.detail}")
    else:
        print(f"  {FAIL} {diag.detail}")
        problems += 1

    print("\n== Configuración ==")
    print(f"  archivo           {config_path()}")
    print(f"  caché de clips    {cache_dir()}")
    bpf = frame_bytes(cfg.video.width, cfg.video.height, cfg.video.pixel_format)
    print(
        f"  salida            {cfg.video.width}x{cfg.video.height} a {cfg.video.fps} FPS "
        f"({cfg.video.pixel_format.upper()}, {bpf / 1024:.0f} KB/fotograma, "
        f"{bpf * cfg.video.fps / 1e6:.1f} MB/s de caché)"
    )
    errors = cfg.validate(require_clips=False)
    for err in errors:
        print(f"  {FAIL} {err}")
        problems += 1
    for warn in cfg.warnings():
        print(f"  {WARN} {warn}")
    for label, clip in (("habla", cfg.video.speaking_clip), ("silencio", cfg.video.silent_clip)):
        if not clip:
            print(f"  {WARN} clip de {label}: sin seleccionar")
        elif not Path(clip).exists():
            print(f"  {FAIL} clip de {label}: no se encuentra {clip}")
            problems += 1
        else:
            print(f"  {OK} clip de {label}: {clip}")

    print()
    if problems:
        print(f"{problems} problema(s) que impiden funcionar por completo.")
        return 1
    print("Todo listo.")
    return 0


def cmd_cameras(args: argparse.Namespace) -> int:
    """Lista las cámaras: lo registrado (por ámbito) y lo que enumera DirectShow."""
    from .output.dshow import DShowError, enumerate_video_inputs
    from .output.filters import bundled_filters, detect_backends, filters_dir, list_directshow_cameras

    cameras = list_directshow_cameras()
    try:
        seen = enumerate_video_inputs()
    except DShowError as exc:
        print(f"{WARN} No se pudo enumerar DirectShow: {exc}")
        seen = []
    if not cameras and not seen:
        print(f"{FAIL} No se pudo enumerar ninguna cámara DirectShow.")
        return 1

    virtual = [c for c in cameras if c.backend]
    print("Cámaras virtuales registradas que esta aplicación puede alimentar:\n")
    if virtual:
        for cam in virtual:
            print(f"  {OK if cam.dll_exists else FAIL} {cam.label}")
            print(f"       CLSID {cam.clsid}")
            print(f"       DLL   {cam.dll_path or '(sin ruta)'}")
    else:
        print(f"  {FAIL} Ninguna. Ejecuta «camvoice-cli install-camera».")

    print(f"\nLo que enumera DirectShow en este proceso ({_process_bits()} bits):\n")
    for dev in seen:
        print(f"  - {dev.friendly_name or '(sin nombre)'}")
    if not seen:
        print("  (nada)")

    arches = {c.architecture for c in virtual}
    if virtual and "32-bit" not in arches:
        print(
            f"\n  {WARN} Solo hay filtro de 64 bits registrado. Una aplicación de "
            "videollamada de 32 bits no verá la cámara."
        )

    print(f"\nBackends utilizables: {', '.join(detect_backends()) or 'ninguno'}")
    bundled = bundled_filters()
    print(
        "Filtro propio incluido: "
        + (", ".join(p.name for p in bundled.values()) if bundled else f"no ({filters_dir()})")
    )
    return 0


def _process_bits() -> int:
    import struct

    return 8 * struct.calcsize("P")


def _current_user() -> str:
    import os

    return f"{os.environ.get('USERDOMAIN', '?')}\\{os.environ.get('USERNAME', '?')}"


def _is_elevated() -> bool | None:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return None


def cmd_install_camera(args: argparse.Namespace) -> int:
    """Registra el filtro de cámara virtual **solo para el usuario actual**.

    Códigos de salida: 0 instalado y visto por DirectShow; 1 error; 2 conflicto
    con un registro existente (del sistema o de otro programa), sin cambios.
    """
    from .output.dshow import DShowError, enumerate_video_inputs
    from .output.filters import (
        UNITYCAPTURE_FILTERS,
        FilterConflict,
        FilterError,
        install_for_user,
        uninstall_for_user,
    )

    dll = Path(args.dll) if args.dll else None
    try:
        if args.uninstall:
            report = uninstall_for_user()
        else:
            report = install_for_user(dll, camera_name=args.name)
    except FilterConflict as exc:
        print(f"{FAIL} {exc}")
        return 2
    except FilterError as exc:
        print(f"{FAIL} {exc}")
        return 1

    if report.locked_summary():
        print(f"{WARN} {report.locked_summary()}")
    if args.uninstall:
        for line in report.summary().splitlines():
            print(f"{OK} {line}")
        own = {spec.filter_clsid for spec in UNITYCAPTURE_FILTERS.values()}
        try:
            left = [d for d in enumerate_video_inputs() if d.clsid in own]
        except DShowError as exc:
            print(f"{WARN} No verificable: no se pudo enumerar DirectShow ({exc}).")
            return 0
        if left:
            # Solo puede venir de un registro del sistema, que no se toca.
            names = ", ".join(d.friendly_name for d in left)
            print(f"{WARN} DirectShow sigue viendo (registro del sistema, no se toca): {names}")
        else:
            print(f"{OK} DirectShow ya no enumera ninguna cámara de camvoice.")
        return 0

    print(f"{OK} Registrada solo para el usuario {_current_user()}, sin permisos de administrador:")
    for line in report.summary().splitlines():
        print(f"     {line}")

    if "64-bit" in report.installed and _process_bits() == 64:
        print("\nComprobando que DirectShow la ve...")
        try:
            names = {d.friendly_name for d in enumerate_video_inputs()}
        except DShowError as exc:
            print(f"{FAIL} No se pudo enumerar DirectShow: {exc}")
            return 1
        if args.name not in names:
            print(f"{FAIL} El registro se escribió pero DirectShow no enumera «{args.name}».")
            return 1
        print(f"{OK} DirectShow (64 bits) enumera «{args.name}».")
    print("\nPrueba completa (32 bits y fotogramas reales): camvoice-cli verify-camera")
    print(
        "Recuerda: las aplicaciones de videollamada enumeran las cámaras al "
        "arrancar. Cierra y vuelve a abrir Teams, Zoom o el navegador."
    )
    return 0


def cmd_verify_camera(args: argparse.Namespace) -> int:
    """Comprueba la cámara de punta a punta y dice qué se pudo verificar.

    Sale con 0 solo si nada falló. Lo que no se puede comprobar desde aquí se
    marca «No verificable» y no cuenta ni como éxito ni como fallo.
    """
    from .output.dshow import DShowError, enumerate_video_inputs, enumerate_video_inputs_x86, roundtrip
    from .output.filters import UNITYCAPTURE_FILTERS, sha256_of, user_filters_dir, user_install_status

    failures = 0
    bits = _process_bits()
    print("== Contexto ==")
    print(f"  usuario           {_current_user()}")
    elevated = _is_elevated()
    print(f"  proceso elevado   {'sí' if elevated else 'no' if elevated is not None else 'desconocido'}")
    print(f"  arquitectura      {bits} bits")
    print(f"  carpeta de DLL    {user_filters_dir()}")
    if elevated:
        print(
            f"  {WARN} Proceso elevado: COM no usa el registro por usuario en procesos "
            "elevados. Repite la verificación sin «Ejecutar como administrador»."
        )

    print("\n== Registro (HKCU = este usuario; HKLM = sistema, solo lectura) ==")
    names: dict[str, str] = {}
    for arch, status in user_install_status().items():
        spec = UNITYCAPTURE_FILTERS[arch]
        mark = {"instalado": OK, "roto": FAIL, "no instalado": FAIL}.get(status.state, WARN)
        print(f"  {mark} {arch}: {status.label}")
        print(f"       CLSID {spec.filter_clsid}")
        if status.dll_path:
            print(f"       DLL   {status.dll_path}")
            if Path(status.dll_path).is_file():
                digest = sha256_of(Path(status.dll_path))
                same = "coincide" if digest == spec.sha256 else "NO COINCIDE"
                print(f"       SHA-256 {digest} ({same})")
        for problem in status.problems:
            print(f"       {problem}")
        if status.state in ("roto", "no instalado"):
            failures += 1
        if status.name:
            names[arch] = status.name
    camera_name = args.name or names.get("64-bit") or names.get("32-bit") or DEFAULT_CAMERA_NAME

    print(f"\n== Enumeración DirectShow de «{camera_name}» ==")
    for arch, enumerate_ in (("64-bit", enumerate_video_inputs), ("32-bit", enumerate_video_inputs_x86)):
        expected = UNITYCAPTURE_FILTERS[arch].filter_clsid
        if arch == "64-bit" and bits != 64:
            print(f"  {WARN} {arch}: No verificable desde un proceso de 32 bits.")
            continue
        try:
            devices = enumerate_()
        except DShowError as exc:
            print(f"  {WARN} {arch}: No verificable ({exc})")
            continue
        hit = [d for d in devices if d.friendly_name == camera_name]
        if hit and hit[0].clsid == expected:
            print(f"  {OK} {arch}: un proceso de {arch[:2]} bits la ve ({hit[0].clsid})")
        elif hit:
            print(f"  {FAIL} {arch}: se ve con un CLSID inesperado {hit[0].clsid}")
            failures += 1
        else:
            print(f"  {FAIL} {arch}: un proceso de {arch[:2]} bits no la ve")
            failures += 1

    print("\n== Fotogramas (pyvirtualcam -> grafo DirectShow) ==")
    if args.no_frames:
        print(f"  {WARN} No ejecutada (--no-frames).")
    elif bits != 64:
        print(f"  {WARN} No verificable desde un proceso de 32 bits.")
    else:
        result = roundtrip(camera_name, seconds=args.seconds)
        if result.ok:
            print(
                f"  {OK} 64-bit: {result.samples_matching}/{result.samples} muestras con los "
                f"colores enviados, {result.color_changes} cambios, {result.width}x{result.height}"
            )
        elif result.error.startswith("No verificable"):
            print(f"  {WARN} {result.error}")
        else:
            print(f"  {FAIL} {result.error}")
            failures += 1
        print(f"  {WARN} 32-bit: No verificable con fotogramas desde este proceso.")

    print()
    if failures:
        print(f"RESULTADO: FALLO ({failures} comprobación(es) no superada(s)).")
        return 1
    print("RESULTADO: correcto en todo lo que se pudo verificar.")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from .video.cache import ClipCacheError, probe_clip

    rc = 0
    for raw in args.videos:
        try:
            probe = probe_clip(raw)
        except ClipCacheError as exc:
            print(f"{FAIL} {raw}: {exc}")
            rc = 1
            continue
        print(f"{OK} {Path(raw).name}")
        print(f"     resolución   {probe.width}x{probe.height} ({probe.aspect:.3f}:1)")
        print(f"     cadencia     {probe.fps:.3f} FPS")
        print(f"     fotogramas   {probe.frame_count} ({probe.duration_s:.2f} s)")
        print(f"     códec        {probe.fourcc or 'desconocido'}")
    return rc


def cmd_prepare(args: argparse.Namespace) -> int:
    from .controller import Session, SessionError

    cfg = _load(args)
    session = Session(cfg)
    last = [-1]

    def progress(done: int, total: int, message: str) -> None:
        if total and done != last[0]:
            pct = min(100, int(done * 100 / total))
            print(f"\r  {message}: {pct:3d}% ({done}/{total})", end="", flush=True)
            last[0] = done
        elif not total:
            print(f"\r  {message}: {done} fotogramas", end="", flush=True)

    try:
        clips = session.prepare(progress=progress, force=args.force)
    except SessionError as exc:
        print(f"\n{FAIL} {exc}")
        return 1
    print()
    print(f"{OK} {clips.speaking.describe()}")
    print(f"{OK} {clips.silent.describe()}")
    print(f"     caché total: {clips.total_mb:.0f} MB en {cache_dir()}")
    for warn in clips.warnings:
        print(f"  {WARN} {warn}")
    session.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .controller import Session, SessionError

    cfg = _load(args)
    session = Session(cfg)

    print("Preparando clips...")
    try:
        session.prepare(force=args.force, load_to_ram=args.ram)
    except SessionError as exc:
        print(f"{FAIL} {exc}")
        return 1
    assert session.clips is not None
    print(f"  {session.clips.speaking.describe()}")
    print(f"  {session.clips.silent.describe()}")
    for warn in session.clips.warnings:
        print(f"  {WARN} {warn}")

    stop = {"now": False}

    def handle(signum, frame):  # noqa: ANN001
        stop["now"] = True

    signal.signal(signal.SIGINT, handle)

    session.start()
    started = time.perf_counter()
    label = f"{args.duration:.0f} s" if args.duration else "Ctrl+C para detener"
    if session.engine is not None:
        print(f"  efectos: {session.engine.effects.describe()}")
    print(f"\nEn marcha ({label}).\n")

    try:
        while not stop["now"]:
            if args.duration and time.perf_counter() - started >= args.duration:
                break
            status = session.status()
            if args.quiet:
                time.sleep(0.5)
                continue
            v, r = status.voice, status.render
            line = (
                f"\r{status.headline:<16} "
                f"nivel {_bar(v.rms_db)} {v.rms_db:6.1f} dB  "
                f"piso {v.noise_floor_db:6.1f}  "
                f"FPS {r.fps_effective:5.2f}  "
                f"ms/frame {r.avg_frame_ms:5.2f}  "
                f"cambios {r.transitions:3d}  "
            )
            sys.stdout.write(line[:150])
            sys.stdout.flush()
            if v.error:
                print(f"\n{FAIL} audio: {v.error}")
            elif v.signal_lost:
                print(f"\n{WARN} {v.hint}")
            if r.error:
                print(f"\n{FAIL} salida: {r.error}")
            if r.warning:
                print(f"\n{WARN} {r.warning}")
            time.sleep(0.2)
    finally:
        print("\nDeteniendo...")
        # El estado se lee ANTES de parar: `stop()` descarta el motor y a partir
        # de ahí `status()` devuelve la instantánea vacía por defecto, de modo
        # que el resumen informaría siempre de cero fotogramas.
        status = session.status()
        summary = session.metrics.summary() if session.metrics else None
        session.stop()
        print(f"  fotogramas emitidos: {status.render.frames_sent}")
        print(f"  cambios de estado:   {status.render.transitions}")
        print(f"  FPS efectivos:       {status.render.fps_effective:.2f}")
        if summary is not None:
            print()
            print(summary.render())
        session.close()
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Mide el régimen estacionario con y sin cámara virtual."""
    from .controller import Session, SessionError
    from .output.sink import NullSink

    cfg = _load(args)
    cfg.metrics.enabled = True
    cfg.metrics.interval_s = min(cfg.metrics.interval_s, 1.0)

    session = Session(cfg)
    print("Preparando clips...")
    try:
        session.prepare(force=args.force, load_to_ram=args.ram)
    except SessionError as exc:
        print(f"{FAIL} {exc}")
        return 1

    sink = NullSink() if args.no_virtual_cam else None
    target = "sin salida (coste del motor)" if sink else "cámara virtual"
    print(f"Midiendo {args.duration:.0f} s con {target}...")
    print("Habla y calla alternadamente si quieres incluir transiciones.\n")

    session.start(sink=sink)
    deadline = time.perf_counter() + args.duration
    try:
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            status = session.status()
            sys.stdout.write(
                f"\r  {remaining:5.0f} s restantes   estado {status.headline:<16} "
                f"FPS {status.render.fps_effective:5.2f}   "
                f"ms/frame {status.render.avg_frame_ms:5.2f}"
            )
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        print("\n")
        summary = session.metrics.summary() if session.metrics else None
        session.stop()
        session.close()

    if summary is None:
        print(f"{FAIL} No se recogieron métricas.")
        return 1
    print(summary.render())

    target_fps = cfg.video.fps
    print()
    verdicts = [
        (
            "FPS sostenidos",
            summary.fps_mean >= target_fps * 0.97,
            f"{summary.fps_mean:.2f} frente a {target_fps} objetivo",
        ),
        (
            "CPU bajo el 25 %",
            summary.cpu_mean < 25.0,
            f"{summary.cpu_mean:.1f} % de un núcleo (p95 {summary.cpu_p95:.1f} %) = "
            f"{summary.cpu_norm_mean:.1f} % de la máquina",
        ),
        (
            "Memoria",
            summary.memory_stable,
            f"{summary.rss_slope_mb_per_min:+.2f} MB/min -> {summary.memory_verdict}"
            + (
                "; repite con --duration 1800 para decidir CA-08"
                if summary.memory_verdict == "no concluyente"
                else ""
            ),
        ),
    ]
    for name, ok, detail in verdicts:
        print(f"  {OK if ok else FAIL} {name:<22}{detail}")
    return 0 if all(v[1] for v in verdicts) else 2


def cmd_vad_test(args: argparse.Namespace) -> int:
    """Escucha el micrófono y muestra la cronología del detector.

    Es la herramienta para ajustar sensibilidad (CA-04 y CA-05) sin necesidad de
    los videos ni de la cámara virtual.
    """
    from .audio.pipeline import AudioPipeline

    cfg = _load(args)
    pipeline = AudioPipeline(cfg)
    pipeline.start()

    print(f"Escuchando {args.duration:.0f} s. Habla, calla y haz ruido de fondo.\n")
    print(f"{'t (s)':>7}  {'estado':<9} {'trama':<7} {'nivel':>7} {'piso':>7} {'SNR':>6}  medidor")

    started = time.perf_counter()
    changes: list[tuple[float, str]] = []
    previous = None
    try:
        while time.perf_counter() - started < args.duration:
            s = pipeline.status()
            t = time.perf_counter() - started
            if s.error:
                print(f"\n{FAIL} {s.error}")
                time.sleep(1.0)
                continue
            if previous is None:
                previous = s.state.value  # el estado inicial no es un cambio
            elif s.state.value != previous:
                changes.append((t, s.state.value))
                previous = s.state.value
            print(
                f"{t:7.2f}  {s.state.value:<9} "
                f"{'voz' if s.voiced else 'silencio':<7} "
                f"{s.rms_db:7.1f} {s.noise_floor_db:7.1f} {s.snr_db:6.1f}  {_bar(s.rms_db)}"
                + ("  calibrando" if s.calibrating else "")
                + (f"  eco:{s.echo_state}" if s.echo_state in ("echo", "double") else ""),
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()

    status = pipeline.status()
    if status.hint:
        print(f"\n{WARN} {status.hint}")
    print(f"\nBackend: {status.backend}   dispositivo: {status.device_name}")
    print(
        f"Audio a {status.samplerate} Hz"
        + (f" (remuestreado desde {status.resampled_from} Hz)" if status.resampled_from != status.samplerate else "")
    )
    print(f"Tramas analizadas: {status.frames}   desbordes: {status.overflows}")
    if status.echo_reference:
        print(
            f"Referencia de eco: {status.echo_reference}   "
            f"tramas de voz vetadas por eco remoto: {status.echo_vetoes}"
        )
    else:
        print(f"Referencia de eco: ninguna ({status.echo_reference_error or 'desactivada'})")
    print(f"Cambios de estado: {len(changes)}")
    for t, state in changes:
        print(f"  {t:6.2f} s -> {state}")
    if not changes:
        print("  (ninguno: el detector se mantuvo estable)")
    if status.frames:
        elapsed = time.perf_counter() - started
        rate = len(changes) / max(1e-6, elapsed / 60.0)
        print(f"\nTasa de cambios: {rate:.1f} por minuto")
        if rate > 30:
            print(f"{WARN} Demasiados cambios: sube --aggressiveness o --release.")
    return 0


def cmd_teams_test(args: argparse.Namespace) -> int:
    """Muestra en vivo lo que CamVoice lee del micrófono de Teams (solo lectura).

    Sirve para comprobar, con la versión instalada de Teams, si el botón de
    micrófono expone `AutomationId = microphone-button` y qué etiqueta tiene
    con el micrófono activo y silenciado. No pulsa nada ni cambia el mute.
    """
    from .meeting import MeetingState, TeamsUiaProvider, default_provider, state_label

    cfg = _load(args)
    provider = default_provider(
        poll_ms=cfg.speech.teams_poll_ms, unmuted_expiry_ms=cfg.speech.unmuted_expiry_ms
    )
    if not isinstance(provider, TeamsUiaProvider):
        print(f"{FAIL} {provider.snapshot().detail}")
        return 1
    provider.start()
    print(
        f"Leyendo Teams durante {args.duration:.0f} s (cada {provider.poll_ms} ms; "
        f"UNMUTED caduca a los {provider.unmuted_expiry_ms} ms).\n"
        "Silencia y reactiva el micrófono en Teams para ver los cambios.\n"
    )
    print(f"{'t (s)':>7}  {'estado':<11} {'edad':>7}  {'vent.':>5} {'bot.':>4} {'lect. ms':>8}  etiqueta / detalle")
    started = time.perf_counter()
    changes: list[tuple[float, str]] = []
    previous = None
    try:
        while time.perf_counter() - started < args.duration:
            s = provider.snapshot()
            t = time.perf_counter() - started
            if previous is None:
                previous = s.state
            elif s.state is not previous:
                changes.append((t, s.state.value))
                previous = s.state
            edad = s.age_ms()
            print(
                f"{t:7.2f}  {s.state.value:<11} "
                f"{(f'{edad:.0f}' if edad != float('inf') else '-'):>7}  "
                f"{s.windows:>5} {s.buttons:>4} {s.read_ms:8.1f}  "
                + (f"«{s.label}»" if s.label else "")
                + (f" {s.detail}" if s.detail else "")
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        provider.stop()

    print(f"\nLecturas: {provider.reads}   exploraciones completas: {provider.rescans}   errores: {provider.errors}")
    print(f"Cambios de estado: {len(changes)}")
    for t, state in changes:
        print(f"  {t:6.2f} s -> {state} ({state_label(MeetingState(state))})")
    if not changes:
        print("  (ninguno)")
    if previous is MeetingState.NO_MEETING:
        print(
            f"\n{WARN} No se vio ninguna reunión. Si Teams estaba en una reunión, "
            "su versión no expone el botón «microphone-button»: el modo automático "
            "quedará bloqueado y conviene usar el control manual."
        )
    return 0


def cmd_make_clips(args: argparse.Namespace) -> int:
    from .testclips import make_test_clips, make_test_library

    if args.library:
        lib = make_test_library(
            Path(args.outdir),
            width=args.width,
            height=args.height,
            fps=args.fps,
            seconds=args.seconds,
        )
        print(f"{OK} clip de habla:     {lib['speaking']}")
        print(f"{OK} clip de silencio:  {lib['silent']}")
        print(f"{OK} silencio extra:    {lib['silent2']} (encuadre y luz distintos a propósito)")
        for name, path in lib["gestures"].items():
            print(f"{OK} gesto {name:<12} {path}")
        print(f"{OK} manifiesto:        {lib['manifest']}")
        print("\nPruébalos con:")
        print(
            f'  camvoice run --speaking "{lib["speaking"]}" --silent "{lib["silent"]}" '
            f'--manifest "{lib["manifest"]}" --no-virtual-cam --duration 20'
        )
        return 0

    speaking, silent = make_test_clips(
        Path(args.outdir),
        width=args.width,
        height=args.height,
        fps=args.fps,
        seconds=args.seconds,
    )
    print(f"{OK} clip de habla:    {speaking}")
    print(f"{OK} clip de silencio: {silent}")
    print("\nSon clips sintéticos para validar la cadena completa sin grabar nada.")
    print("Pruébalos con:")
    print(f'  camvoice run --speaking "{speaking}" --silent "{silent}"')
    return 0


def _prepare_session(args: argparse.Namespace, cfg: AppConfig):  # noqa: ANN202
    """Prepara clips (y en modo director, índices) mostrando el progreso."""
    from .controller import Session, SessionError

    session = Session(cfg)
    last = [""]

    def progress(done: int, total: int, message: str) -> None:
        line = f"\r  {message[:44]:<44} {done:>6}/{max(total, done):<6}"
        if line != last[0]:
            sys.stdout.write(line)
            sys.stdout.flush()
            last[0] = line

    try:
        clips = session.prepare(
            progress=progress,
            force=getattr(args, "force", False),
            load_to_ram=getattr(args, "ram", False),
        )
    except SessionError as exc:
        print(f"\n{FAIL} {exc}")
        session.close()
        return None, None
    print()
    return session, clips


def cmd_index(args: argparse.Namespace) -> int:
    """Prepara cachés e índices del modo director y resume la biblioteca."""
    cfg = _load(args)
    cfg.video.playback = "director"
    started = time.perf_counter()
    session, clips = _prepare_session(args, cfg)
    if session is None:
        return 1
    elapsed = time.perf_counter() - started
    try:
        print(f"Modo efectivo: {clips.mode}   ({elapsed:.1f} s)")
        for title, pool in (("Silencio", clips.silent_pool), ("Habla", clips.speaking_pool)):
            for c in pool:
                idx = c.index
                print(
                    f"  {title:<9} {c.name:<28} {idx.frame_count:>6} fot.  d_ok {idx.d_ok:.3f}  "
                    f"cara {'sí' if idx.face_found else 'no'}  rango boca {idx.mouth_range:.2f}"
                )
        for name, c in clips.gestures.items():
            print(f"  Gesto     {name:<28} {c.index.frame_count:>6} fot.")
        print(f"Caché total: {clips.total_mb:.0f} MB")
        for w in clips.warnings:
            print(f"{WARN} {w}")
        return 0 if clips.mode == "director" else 2
    finally:
        session.close()


def cmd_transitions(args: argparse.Namespace) -> int:
    """Mide en los clips reales el coste y la espera de las transiciones."""
    from .video.trials import measure

    cfg = _load(args)
    cfg.video.playback = "director"
    session, clips = _prepare_session(args, cfg)
    if session is None:
        return 1
    try:
        if clips.mode != "director":
            print(f"{FAIL} El modo director no está disponible: " + "; ".join(clips.warnings))
            return 2
        print(
            f"Clips: {len(clips.silent_pool)} silencio, {len(clips.speaking_pool)} habla, "
            f"{len(clips.gestures)} gestos/frases · {cfg.video.fps} FPS · "
            f"{args.repeats} repeticiones por escenario (reloj simulado)"
        )
        started = time.perf_counter()
        summary = measure(
            cfg.video, clips.silent_pool, clips.speaking_pool, clips.gestures,
            repeats=args.repeats, speaking_actions=sorted(clips.speaking_actions),
            progress=_print_progress,
        )
        print()
        for line in summary.lines():
            print(line)
        print(f"({time.perf_counter() - started:.1f} s)")
        return 0
    finally:
        session.close()


def _print_progress(done: int, total: int, text: str) -> None:
    pct = 100.0 * done / total if total else 100.0
    print(f"\r  {pct:5.1f} %  {text[:90]:<90}", end="", flush=True)


def cmd_library(args: argparse.Namespace) -> int:
    """Detecta, valida, importa o deshace la importación de las escenas numeradas."""
    from .scenes import EXPECTED_COUNT, SKIPPED, scene_by_number

    def scene_label(n: int) -> str:
        s = scene_by_number(n)
        return s.label if s else '?'
    from .video.importer import (
        STATUS_ERROR,
        STATUS_LABELS,
        STATUS_READY,
        ImportCancelled,
        default_source_folder,
        import_library,
        rollback_library,
        scan_folder,
        space_needed,
        validate_scan,
    )
    from .video.library import manifest_path

    cfg = _load(args)
    path = manifest_path(cfg)
    if args.rollback:
        msg = rollback_library(cfg)
        cfg.save(Path(args.config) if getattr(args, "config", None) else None)
        print(f"{OK} {msg}")
        return 0

    folder = Path(args.folder).expanduser() if args.folder else default_source_folder()
    try:
        scan = scan_folder(folder)
    except FileNotFoundError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"Carpeta: {folder}")
    print(f"  {scan.summary()}")
    print(f"  Archivos ignorados (sin número o no son video): {len(scan.ignored)}")
    if args.copy:
        print(f"  Espacio necesario para copiar: {space_needed(scan) / 1e9:.2f} GB")
    if not (args.validate or args.do_import):
        for number, entry in sorted(scan.entries.items()):
            names = ", ".join(p.name for p in entry.paths)
            mark = FAIL if entry.ambiguous else OK
            print(f"  {mark} {number:>2}  {scene_label(number):<34} {names}")
        return 0 if len(scan.matched) == EXPECTED_COUNT and not scan.ambiguous else 2

    started = time.perf_counter()
    try:
        probes = validate_scan(scan, path.parent, progress=_print_progress, force=args.force)
        print()
        counts = {s: 0 for s in STATUS_LABELS}
        for number, info in sorted(probes.items()):
            counts[info.status] = counts.get(info.status, 0) + 1
            if info.status != STATUS_READY or info.issues:
                mark = FAIL if info.status == STATUS_ERROR else WARN
                print(f"  {mark} n.º {number:>2} {scene_label(number)}: " + "; ".join(info.issues))
        print(
            "  Validación: "
            + ", ".join(f"{STATUS_LABELS[s]} {n}" for s, n in counts.items())
            + f"   ({time.perf_counter() - started:.1f} s)"
        )
        if not args.do_import:
            return 0 if counts.get(STATUS_ERROR, 0) == 0 else 2
        lib, warnings = import_library(
            cfg, scan, probes, copy=args.copy, apply_main_clips=not args.keep_main,
            progress=_print_progress,
        )
        print()
    except ImportCancelled as exc:
        print(f"\n{WARN} {exc}")
        return 130
    cfg.save(Path(args.config) if getattr(args, "config", None) else None)
    imported = sum(1 for e in lib.scenes.values() if e.status != STATUS_ERROR)
    print(
        f"{OK if imported == EXPECTED_COUNT else WARN} {imported}/{EXPECTED_COUNT} importadas "
        f"(+{len(SKIPPED)} omitidas a propósito)   manifiesto: {path}"
    )
    for w in warnings:
        print(f"  {WARN} {w}")
    print(f"  Silencio principal: {cfg.video.silent_clip}")
    print(f"  Habla principal:    {cfg.video.speaking_clip}")
    print("  Siguiente paso: `camvoice index` (prepara cachés e índices).")
    return 0 if imported == EXPECTED_COUNT else 2


def cmd_gestures(args: argparse.Namespace) -> int:
    """Lista, valida o asigna gestos del manifiesto."""
    from .gestures import (
        GESTURES,
        SPEAKING_SUFFIX,
        HotkeyError,
        gesture_label,
        is_known_gesture,
        validate_hotkey,
    )
    from .video.library import load_library, manifest_path, save_library

    cfg = _load(args)
    loaded = load_library(cfg)
    lib = loaded.library
    path = manifest_path(cfg)

    if args.set:
        name, clip = args.set
        if not is_known_gesture(name):
            print(
                f"{FAIL} Gesto desconocido «{name}». Válidos: {', '.join(GESTURES)} "
                f"(o su variante «<gesto>{SPEAKING_SUFFIX}» para usarla mientras hablas)"
            )
            return 1
        if not Path(clip).is_file():
            print(f"{FAIL} No existe el clip {clip}")
            return 1
        if args.hotkey and name not in GESTURES:
            print(f"{FAIL} Las variantes «{SPEAKING_SUFFIX}» no tienen atajo propio: usan el del gesto base.")
            return 1
        if args.hotkey:
            try:
                validate_hotkey(args.hotkey)
            except HotkeyError as exc:
                print(f"{FAIL} {exc}")
                return 1
        lib.set_gesture(name, str(Path(clip).resolve()), args.hotkey)
        problems = lib.validate(check_files=True)
        if problems:
            for problem in problems:
                print(f"{FAIL} {problem}")
            return 1
        save_library(lib, path)
        print(f"{OK} «{gesture_label(name)}» -> {clip}   (guardado en {path})")
        return 0

    state = "existe" if loaded.exists else "no existe: solo se usan los clips principales"
    print(f"Manifiesto: {path} ({state})")
    print(f"Versión: {lib.version}")
    silent, speaking = lib.pools(cfg)
    for title, pool in (("silencio", silent), ("habla", speaking)):
        print(f"Grupo de {title} ({len(pool)}):")
        for clip_path in pool:
            print(f"  {OK if Path(clip_path).is_file() else FAIL} {clip_path}")
    keys = lib.hotkeys()
    print("Gestos:")
    for name in GESTURES:
        entry = lib.gestures.get(name)
        key = keys.get(name, "")
        if entry is None or not entry.clip:
            print(f"  {WARN} {gesture_label(name):<18} sin clip   {key}")
            continue
        ok = Path(entry.clip).is_file() and entry.enabled
        extra = "" if entry.enabled else " (desactivado)"
        print(f"  {OK if ok else FAIL} {gesture_label(name):<18} {Path(entry.clip).name}{extra}   {key}")
    variants = sorted(n for n in lib.gestures if n.endswith(SPEAKING_SUFFIX))
    if variants:
        print("Variantes para usar mientras hablas:")
        for name in variants:
            entry = lib.gestures[name]
            ok = bool(entry.clip) and Path(entry.clip).is_file() and entry.enabled
            print(f"  {OK if ok else FAIL} {gesture_label(name):<28} {Path(entry.clip).name if entry.clip else '-'}")
    print(f"Cancelar gesto: {cfg.gestures.cancel_hotkey}")
    print(f"Pausar / reanudar video: {cfg.gestures.pause_hotkey}")
    issues = list(loaded.issues) + lib.validate(check_files=True)
    for issue in issues:
        print(f"{WARN} {issue}")
    return 0 if not issues else 2


def cmd_soak(args: argparse.Namespace) -> int:
    """Prueba de resistencia sin micrófono ni cámara (voz sintética)."""
    from .soak import run_soak, simulate
    from .video.director import Director

    cfg = _load(args)
    session, clips = _prepare_session(args, cfg)
    if session is None:
        return 1
    for w in clips.warnings:
        print(f"{WARN} {w}")
    try:
        if args.simulate:
            if clips.mode != "director":
                print(f"{FAIL} La simulación necesita el modo director.")
                return 1
            seed = cfg.video.seed if cfg.video.seed is not None else args.script_seed

            def make(clock):  # noqa: ANN001, ANN202
                return Director(
                    cfg.video,
                    clips.silent_pool,
                    clips.speaking_pool,
                    clips.gestures,
                    gestures_cfg=cfg.gestures,
                    clock=clock,
                    seed=seed,
                    speaking_actions=clips.speaking_actions,
                )

            print(f"Simulando {args.duration / 3600:.2f} h de reunión con reloj falso...")
            res = simulate(
                make,
                args.duration,
                fps=cfg.video.fps,
                gesture_every_s=args.gesture_every,
                seed=args.script_seed,
                pause_every_s=args.pause_every,
            )
            print(res.render())
            ok = (
                res.gestures_stuck == 0
                and res.discontinuities == 0
                and res.pause_frame_changes == 0
                and res.black_frames == 0
            )
            return 0 if ok else 2

        print(f"Soak en tiempo real: {args.duration:.0f} s, modo {clips.mode}, sin cámara virtual.")

        def progress(now: float, status) -> None:  # noqa: ANN001
            r = status.render
            sys.stdout.write(
                f"\r  {now:6.0f}/{args.duration:.0f} s  {status.headline:<22} "
                f"FPS {r.fps_effective:5.2f}  tardíos {r.late_frames:<5}"
            )
            sys.stdout.flush()

        try:
            res = run_soak(
                session,
                args.duration,
                gesture_every_s=args.gesture_every,
                seed=args.script_seed,
                progress=None if args.quiet else progress,
            )
        except KeyboardInterrupt:
            print("\nInterrumpido.")
            return 1
        print("\n")
        print(res.render())
        ok = res.fps_mean >= cfg.video.fps * 0.97 and not res.errors and res.degraded_events == 0
        print(f"\n{OK if ok else FAIL} FPS sostenidos y sin errores")
        return 0 if ok else 2
    finally:
        session.close()


def cmd_gui(args: argparse.Namespace) -> int:
    from .ui.app import run_gui

    cfg = _load(args)
    return run_gui(cfg, config_file=Path(args.config) if args.config else None)


def cmd_config(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else config_path()
    if args.reset:
        if path.exists():
            path.unlink()
            print(f"{OK} Configuración borrada: {path}")
        else:
            print("No había configuración guardada.")
        return 0
    cfg = AppConfig.load(path)
    import json

    print(f"# {path}{'' if path.exists() else '  (no existe: valores por defecto)'}")
    print(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    from .video.cache import prune_cache

    freed = prune_cache(set(), directory=cache_dir())
    print(f"{OK} Liberados {freed / 1e6:.0f} MB de {cache_dir()}")
    return 0


# --------------------------------------------------------------------------- #
# Definición del parser
# --------------------------------------------------------------------------- #


def _add_clip_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--speaking", help="Video del usuario hablando.")
    p.add_argument("--silent", help="Video del usuario en silencio.")
    p.add_argument("--width", type=int, help="Ancho de salida (por defecto 640).")
    p.add_argument("--height", type=int, help="Alto de salida (por defecto 480).")
    p.add_argument("--fps", type=int, help="FPS de salida (por defecto 15).")
    p.add_argument("--fit", choices=("cover", "contain", "stretch"), help="Ajuste del encuadre.")
    p.add_argument("--mirror", action="store_true", default=None, help="Espejo horizontal.")
    p.add_argument("--transition", type=int, metavar="MS", help="Duración del fundido.")
    p.add_argument("--loop-mode", choices=("pingpong", "loop"), dest="loop_mode")
    p.add_argument("--pixel-format", choices=("nv12", "bgr"), dest="pixel_format")
    p.add_argument("--force", action="store_true", help="Regenera la caché de clips.")
    p.add_argument("--ram", action="store_true", help="Carga la caché entera en RAM.")
    p.add_argument(
        "--playback",
        choices=("director", "classic"),
        help="director: grupos de clips, boca guiada por la voz y gestos; "
        "classic: el compositor anterior (respaldo).",
    )
    p.add_argument("--manifest", help="Manifiesto de la biblioteca (grupos y gestos).")
    p.add_argument("--seed", type=int, help="Semilla del director (reproducible).")
    p.add_argument(
        "--no-normalize",
        action="store_true",
        dest="no_normalize",
        help="No igualar encuadre ni color entre clips.",
    )


def _add_audio_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mic", type=int, metavar="N", help="Índice de micrófono (ver «devices»).")
    p.add_argument("--vad-backend", choices=("auto", "webrtc", "rms"), dest="vad_backend")
    p.add_argument("--aggressiveness", type=int, choices=(0, 1, 2, 3))
    p.add_argument("--attack", type=int, metavar="MS", help="Voz necesaria para activar.")
    p.add_argument("--release", type=int, metavar="MS", help="Silencio necesario para desactivar.")


def _add_effect_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group(
        "efectos",
        "Porcentajes independientes de la resolución: el 60 % se ve igual a "
        "320x240 que a 720p. Se aplican sobre el fotograma de salida.",
    )
    g.add_argument("--blur", type=float, metavar="PCT", help="Desenfoque, 0-100.")
    g.add_argument("--pixelate", type=float, metavar="PCT", help="Pixelado, 0-100.")
    g.add_argument("--brightness", type=float, metavar="PCT", help="Brillo, -100 a 100.")
    g.add_argument("--contrast", type=float, metavar="PCT", help="Contraste, -100 a 100.")
    g.add_argument("--saturation", type=float, metavar="PCT", help="Saturación, -100 a 100.")
    g.add_argument("--vignette", type=float, metavar="PCT", help="Viñeta, 0-100.")
    g.add_argument("--grain", type=float, metavar="PCT", help="Grano, 0-100.")
    g.add_argument("--zoom", type=float, metavar="X", help="Zoom, 1.0 a 4.0.")
    g.add_argument("--no-effects", action="store_true", help="Desactiva la cadena.")


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-virtual-cam", action="store_true", help="No abrir la cámara virtual.")
    p.add_argument("--backend", help="Backend de pyvirtualcam (obs, unitycapture).")
    p.add_argument("--metrics", action="store_true", help="Registrar métricas en CSV.")
    p.add_argument("--metrics-csv", help="Ruta del CSV de métricas.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camvoice",
        description=(
            "Cámara virtual que reproduce un clip pregrabado tuyo hablando cuando "
            "detecta tu voz y otro en silencio cuando callas."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  camvoice doctor\n"
            "  camvoice devices\n"
            "  camvoice make-clips --outdir .\\clips\n"
            '  camvoice run --speaking hablando.mp4 --silent quieto.mp4 --save\n'
            "  camvoice vad-test --duration 30\n"
            "  camvoice bench --duration 1800 --metrics\n"
            "  camvoice make-clips --library --outdir .\\clips\n"
            "  camvoice library --import\n"
            "  camvoice gestures\n"
            "  camvoice soak --duration 7200\n"
        ),
    )
    parser.add_argument("--config", help="Ruta alternativa del archivo de configuración.")
    parser.add_argument("--save", action="store_true", help="Guarda las opciones dadas.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="Abre la interfaz gráfica (acción por defecto).")
    _add_clip_args(p)
    _add_audio_args(p)
    _add_effect_args(p)
    _add_output_args(p)
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("doctor", help="Comprueba entorno, audio y cámara virtual.")
    _add_output_args(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("devices", help="Lista los micrófonos disponibles.")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("cameras", help="Lista las cámaras registradas en Windows.")
    p.set_defaults(func=cmd_cameras)

    p = sub.add_parser(
        "install-camera",
        help="Registra la cámara virtual solo para tu usuario (sin administrador).",
    )
    p.add_argument("--dll", help="DLL concreta a registrar; por defecto la incluida.")
    p.add_argument(
        "--name",
        default=DEFAULT_CAMERA_NAME,
        help=f"Nombre con el que aparecerá la cámara (por defecto: {DEFAULT_CAMERA_NAME!r}).",
    )
    p.add_argument("--uninstall", action="store_true", help="Quitar el registro por usuario.")
    p.set_defaults(func=cmd_install_camera)

    p = sub.add_parser(
        "verify-camera",
        help="Verifica la cámara: registro, enumeración 64/32 bits y fotogramas reales.",
    )
    p.add_argument("--name", default="", help="Nombre de la cámara; por defecto el registrado.")
    p.add_argument("--no-frames", action="store_true", help="No enviar ni leer fotogramas.")
    p.add_argument("--seconds", type=float, default=4.0, help="Duración de la prueba de fotogramas.")
    p.set_defaults(func=cmd_verify_camera)

    p = sub.add_parser("probe", help="Muestra las propiedades de uno o más videos.")
    p.add_argument("videos", nargs="+")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("prepare", help="Prepara la caché de fotogramas de los clips.")
    _add_clip_args(p)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("run", help="Ejecuta sin interfaz gráfica.")
    _add_clip_args(p)
    _add_audio_args(p)
    _add_effect_args(p)
    _add_output_args(p)
    p.add_argument("--duration", type=float, default=0.0, metavar="S", help="0 = indefinido.")
    p.add_argument("--quiet", action="store_true", help="Sin línea de estado continua.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("bench", help="Mide CPU, memoria y FPS durante un periodo.")
    _add_clip_args(p)
    _add_audio_args(p)
    _add_effect_args(p)
    _add_output_args(p)
    p.add_argument("--duration", type=float, default=120.0, metavar="S")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("vad-test", help="Prueba el detector de voz con el micrófono.")
    _add_audio_args(p)
    p.add_argument("--duration", type=float, default=30.0, metavar="S")
    p.add_argument("--interval", type=float, default=0.25, metavar="S")
    p.set_defaults(func=cmd_vad_test)

    p = sub.add_parser(
        "teams-test",
        help="Muestra el estado del micrófono de Teams que lee CamVoice (solo lectura).",
    )
    p.add_argument("--duration", type=float, default=30.0, metavar="S")
    p.add_argument("--interval", type=float, default=0.25, metavar="S")
    p.set_defaults(func=cmd_teams_test)

    p = sub.add_parser("make-clips", help="Genera clips sintéticos de prueba.")
    p.add_argument("--outdir", default="clips")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--seconds", type=float, default=6.0)
    p.add_argument(
        "--library",
        action="store_true",
        help="Además: segundo silencio, clips de gesto y manifest.json.",
    )
    p.set_defaults(func=cmd_make_clips)

    p = sub.add_parser("index", help="Prepara cachés e índices del modo director.")
    _add_clip_args(p)
    p.set_defaults(func=cmd_index)

    p = sub.add_parser(
        "transitions",
        help="Mide coste y espera de las transiciones sobre los clips reales (sin cámara).",
    )
    _add_clip_args(p)
    p.add_argument("--repeats", type=int, default=20, help="Ensayos por escenario (20).")
    p.set_defaults(func=cmd_transitions)

    p = sub.add_parser(
        "library",
        help="Detecta, valida e importa las grabaciones numeradas (1..66).",
        description=(
            "Sin opciones solo detecta. Los videos originales nunca se modifican ni se "
            "borran; por defecto se referencian (con --copy se copian a la biblioteca)."
        ),
    )
    p.add_argument("--folder", help="Carpeta de origen (por defecto Imágenes\\Camera Roll).")
    p.add_argument("--validate", action="store_true", help="Valida cada video (reanudable).")
    p.add_argument("--import", dest="do_import", action="store_true",
                   help="Valida e importa al manifiesto (idempotente).")
    p.add_argument("--copy", action="store_true", help="Copia los videos en vez de referenciarlos.")
    p.add_argument("--keep-main", dest="keep_main", action="store_true",
                   help="No cambiar los clips principales por los n.º 1 y 2.")
    p.add_argument("--force", action="store_true", help="Revalidar aunque no hayan cambiado.")
    p.add_argument("--rollback", action="store_true", help="Deshace la última importación.")
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("gestures", help="Lista/valida los gestos o asigna un clip a uno.")
    p.add_argument("--manifest", help="Manifiesto de la biblioteca.")
    p.add_argument("--set", nargs=2, metavar=("GESTO", "CLIP"), help="Asigna un clip a un gesto.")
    p.add_argument("--hotkey", help="Atajo del gesto asignado con --set (p. ej. Ctrl+Alt+F1).")
    p.set_defaults(func=cmd_gestures)

    p = sub.add_parser(
        "soak", help="Prueba de resistencia con voz sintética (sin micrófono ni cámara)."
    )
    _add_clip_args(p)
    p.add_argument(
        "--duration", type=float, default=7200.0, metavar="S",
        help="Duración (por defecto 7200 s = 2 h).",
    )
    p.add_argument(
        "--gesture-every", type=float, default=45.0, metavar="S", dest="gesture_every",
        help="Pide un gesto al azar cada S segundos (0 = nunca).",
    )
    p.add_argument(
        "--script-seed", type=int, default=7, dest="script_seed",
        help="Semilla del guion de voz sintética.",
    )
    p.add_argument(
        "--simulate", action="store_true",
        help="Solo lógica, con reloj falso: horas en minutos (no mide FPS).",
    )
    p.add_argument(
        "--pause-every", type=float, default=600.0, metavar="S", dest="pause_every",
        help="Con --simulate: pausa manual de 5 s cada S segundos (0 = nunca).",
    )
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_soak)

    p = sub.add_parser("config", help="Muestra o borra la configuración guardada.")
    p.add_argument("--reset", action="store_true")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("clean", help="Borra la caché de fotogramas.")
    p.set_defaults(func=cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Sin subcomando se abre la interfaz: es lo que espera quien hace doble
        # clic en el ejecutable.
        args = parser.parse_args(["gui", *(argv or [])])
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        return 130
