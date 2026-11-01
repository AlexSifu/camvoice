"""Gestor de atajos globales (ui/hotkeys.py) con una API de Windows simulada."""

from __future__ import annotations

from camvoice.gestures import CANCEL
from camvoice.ui.hotkeys import ERROR_HOTKEY_ALREADY_REGISTERED, HotkeyManager


class FakeApi:
    """Registra en memoria; `busy` simula combinaciones de otra aplicación."""

    def __init__(self, busy=()):  # noqa: ANN001
        self.active: dict[int, tuple[int, int]] = {}
        self.busy = set(busy)
        self.calls: list[tuple[str, int]] = []

    def register(self, hk_id, modifiers, vk):  # noqa: ANN001
        self.calls.append(("register", hk_id))
        if (modifiers, vk) in self.busy:
            return False, ERROR_HOTKEY_ALREADY_REGISTERED
        assert hk_id not in self.active, "doble registro del mismo id"
        self.active[hk_id] = (modifiers, vk)
        return True, 0

    def unregister(self, hk_id):  # noqa: ANN001
        self.calls.append(("unregister", hk_id))
        self.active.pop(hk_id, None)


F1, F2, F12 = 0x70, 0x71, 0x7B
CTRL_ALT = 0x0002 | 0x0001


def make(busy=()):  # noqa: ANN001
    fired: list[str] = []
    api = FakeApi(busy)
    return HotkeyManager(fired.append, api), api, fired


def id_for(mgr: HotkeyManager, name: str) -> int:
    return next(b.hk_id for b in mgr.bindings if b.name == name)


def test_registers_valid_hotkeys_and_dispatches():
    mgr, api, fired = make()
    problems = mgr.apply({"saludar": "ctrl+alt+f1", "sonreir": "Ctrl+Alt+F2", CANCEL: "Ctrl+Alt+F12"})
    assert problems == []
    assert sorted(api.active.values()) == sorted([(CTRL_ALT, F1), (CTRL_ALT, F2), (CTRL_ALT, F12)])
    assert mgr.handle(id_for(mgr, "saludar"))
    assert mgr.handle(id_for(mgr, CANCEL))
    assert fired == ["saludar", CANCEL]
    assert mgr.last_trigger[0] == CANCEL
    assert "Ctrl+Alt+F1 → Saludar" in mgr.describe()


def test_unknown_id_is_not_ours():
    mgr, _, fired = make()
    mgr.apply({"saludar": "Ctrl+Alt+F1"})
    assert not mgr.handle(12345)
    assert fired == []


def test_invalid_and_duplicate_hotkeys_are_reported_not_registered():
    mgr, api, _ = make()
    problems = mgr.apply({
        "saludar": "Ctrl+Alt+F1",
        "sonreir": "Ctrl+Alt+F1",      # duplicado
        "asentir": "Ctrl+Shift+M",      # silenciar en Teams
        "negar": "Ctrl+Alt+2",          # AltGr+2 = @
        "reir": "",                     # sin atajo: no es un error
    })
    text = "\n".join(problems)
    assert "está asignado a «Saludar» y a «Sonreír»" in text
    assert "Teams" in text
    assert "AltGr" in text
    assert len(problems) == 3
    assert list(api.active.values()) == [(CTRL_ALT, F1)]


def test_busy_combination_is_reported_and_others_still_work():
    mgr, api, fired = make(busy={(CTRL_ALT, F1)})
    problems = mgr.apply({"saludar": "Ctrl+Alt+F1", "sonreir": "Ctrl+Alt+F2"})
    assert len(problems) == 1 and "ocupado por otra aplicación" in problems[0]
    assert list(api.active.values()) == [(CTRL_ALT, F2)]
    assert not mgr.handle(id_for(mgr, "saludar"))
    assert mgr.handle(id_for(mgr, "sonreir"))
    assert fired == ["sonreir"]


def test_suspend_releases_combinations_and_resume_restores():
    mgr, api, fired = make()
    mgr.apply({"saludar": "Ctrl+Alt+F1"})
    hk = id_for(mgr, "saludar")
    mgr.suspend()
    assert api.active == {}
    assert mgr.suspended and "suspendidos" in mgr.describe()
    assert not mgr.handle(hk)
    assert mgr.resume() == []
    assert list(api.active.values()) == [(CTRL_ALT, F1)]
    assert mgr.handle(hk)
    assert fired == ["saludar"]


def test_busy_error_is_retried_on_resume():
    mgr, api, _ = make(busy={(CTRL_ALT, F1)})
    assert mgr.apply({"saludar": "Ctrl+Alt+F1"})
    mgr.suspend()
    api.busy.clear()  # la otra aplicación se cerró
    assert mgr.resume() == []
    assert list(api.active.values()) == [(CTRL_ALT, F1)]


def test_apply_while_suspended_does_not_register():
    mgr, api, _ = make()
    mgr.set_enabled(False)
    mgr.apply({"saludar": "Ctrl+Alt+F1"})
    assert api.active == {}
    mgr.set_enabled(True)
    assert len(api.active) == 1


def test_reapply_replaces_previous_set_without_leaks():
    mgr, api, _ = make()
    for _ in range(5):
        mgr.apply({"saludar": "Ctrl+Alt+F1", "sonreir": "Ctrl+Alt+F2"})
    assert len(api.active) == 2
    mgr.apply({"saludar": "Ctrl+Alt+F3"})
    assert list(api.active.values()) == [(CTRL_ALT, 0x72)]
    mgr.clear()
    assert api.active == {}


def test_without_api_everything_is_a_safe_no_op():
    fired: list[str] = []
    mgr = HotkeyManager(fired.append, None)
    assert not mgr.available
    assert mgr.apply({"saludar": "Ctrl+Alt+F1"}) == []
    assert not mgr.handle(0xB100)
    assert "no disponibles" in mgr.describe()


def test_callback_exception_does_not_propagate():
    def boom(_name):  # noqa: ANN001
        raise RuntimeError("fallo")

    api = FakeApi()
    mgr = HotkeyManager(boom, api)
    mgr.apply({"saludar": "Ctrl+Alt+F1"})
    assert mgr.handle(id_for(mgr, "saludar")) is True
