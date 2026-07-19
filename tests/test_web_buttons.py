"""Tests for the web mirror's action surface (WEB-PROTOCOL.md §6, §10).

Run from the repo root:  python -m unittest discover -s tests

The mirror exposes the box's FULL action set, including reboot, shutdown and
AIS transmit. That is a deliberate posture decision (§10), and it rests
entirely on two mechanisms holding:

  1. the ACTIVE screen's declared buttons are the authorisation surface — a
     press for anything else is refused, so a screen cannot be actuated while
     it is not showing;
  2. destructive actions require TWO presses inside a short window.

Mechanism 2 is stricter than the panel on purpose. Settings' Reboot/Shutdown
fire on a single tap there, which is safe because tapping requires standing at
the box; a POST only requires being on the LAN, and this service has no auth.

These tests drive App._apply_web_command directly with a stub app, so the
destructive paths are exercised without a real reboot.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash.app import App  # noqa: E402
from kilodash.screens import SCREENS  # noqa: E402
from kilodash.screens.base import Screen  # noqa: E402


class FakeEvents:
    def __init__(self):
        self.errors = []
        self.snapshots = 0

    def send_error(self, code, detail=""):
        self.errors.append((code, detail))

    def send_snapshot(self, force=False):
        self.snapshots += 1

    def note_tile(self, scr):
        pass


class ButtonScreen(Screen):
    title = "Test"
    tile_id = "test"

    def __init__(self):
        self.pressed = []
        self._buttons = [
            {"id": "safe", "label": "SAFE", "enabled": True, "confirm": False},
            {"id": "boom", "label": "BOOM", "enabled": True, "confirm": True},
            {"id": "off", "label": "OFF", "enabled": False, "confirm": False},
        ]

    def model_buttons(self):
        return self._buttons

    def handle_button(self, bid):
        self.pressed.append(bid)
        return True


class OtherScreen(ButtonScreen):
    title = "Other"
    tile_id = "other"

    def model_buttons(self):
        return [{"id": "elsewhere", "label": "X", "enabled": True,
                 "confirm": False}]


class StubApp:
    """App with just enough wiring to run _apply_web_command."""

    # Borrowed from the REAL App, not reimplemented — a stub that reimplements
    # the behaviour under test proves nothing about the app. open_screen is
    # included precisely because it is what drops a pending confirm.
    WEB_CONFIRM_S = App.WEB_CONFIRM_S
    _apply_web_command = App._apply_web_command
    _reject_web = App._reject_web
    _arm_web_button = App._arm_web_button
    _web_armed = App._web_armed
    open_screen = App.open_screen
    is_launcher = App.is_launcher

    def __init__(self):
        self.screen = ButtonScreen()
        self.other = OtherScreen()
        self.current = self.screen
        self.screens = [self.screen, self.other]
        self.events = FakeEvents()
        self._web_arm = None
        self.dirty = False
        self.woke = 0

    def _wake(self):
        self.woke += 1

    def toast(self, msg, secs=2.5):
        pass

    def go_home(self):
        self.open_screen(self.screens[0])

    @property
    def launcher(self):
        return self.screens[0]

    def press(self, bid):
        return self._apply_web_command({"action": "button_press",
                                        "button": bid})


class Authorisation(unittest.TestCase):
    def setUp(self):
        self.app = StubApp()

    def test_declared_button_is_pressed(self):
        self.app.press("safe")
        self.assertEqual(self.app.screen.pressed, ["safe"])

    def test_undeclared_button_is_refused(self):
        self.app.press("nope")
        self.assertEqual(self.app.screen.pressed, [])
        self.assertEqual(self.app.events.errors[0][0], "bad_command")

    def test_button_from_another_screen_is_refused(self):
        """The box never actuates a screen that is not showing."""
        self.app.press("elsewhere")
        self.assertEqual(self.app.screen.pressed, [])
        self.assertTrue(self.app.events.errors)

    def test_disabled_button_is_refused(self):
        """Several screens gate a control in the DRAW pass, which
        handle_button bypasses entirely — so `enabled` is enforced centrally
        or not at all."""
        self.app.press("off")
        self.assertEqual(self.app.screen.pressed, [])
        self.assertIn("disabled", self.app.events.errors[0][1])


class ConfirmGuard(unittest.TestCase):
    """§10 — the mechanism the full-parity posture rests on."""

    def setUp(self):
        self.app = StubApp()

    def test_first_press_does_not_act(self):
        self.app.press("boom")
        self.assertEqual(self.app.screen.pressed, [],
                         "a single press of a confirm button must not act")

    def test_second_press_acts(self):
        self.app.press("boom")
        self.app.press("boom")
        self.assertEqual(self.app.screen.pressed, ["boom"])

    def test_third_press_re_arms_rather_than_repeating(self):
        """After firing, the arm is cleared — so a further press starts the
        two-press sequence again instead of repeating a destructive action."""
        self.app.press("boom")
        self.app.press("boom")
        self.app.press("boom")
        self.assertEqual(self.app.screen.pressed, ["boom"])

    def test_window_expiry_disarms(self):
        self.app.press("boom")
        tile, bid, _ = self.app._web_arm
        self.app._web_arm = (tile, bid, time.monotonic() - 0.01)
        self.app.press("boom")
        self.assertEqual(self.app.screen.pressed, [],
                         "an expired arm must not be honoured")

    def test_arm_does_not_transfer_to_another_button(self):
        """Arming BOOM must not let a different press fire it, and must not
        make an unrelated button destructive."""
        self.app.press("boom")
        self.app.press("safe")
        self.assertEqual(self.app.screen.pressed, ["safe"])
        self.assertNotIn("boom", self.app.screen.pressed)

    def test_arm_drops_on_navigation(self):
        """A confirm must be unambiguous about what it confirms, so leaving
        the screen cancels it."""
        self.app.press("boom")
        self.app.open_screen(self.app.other)
        self.app.open_screen(self.app.screen)
        self.app.press("boom")
        self.assertEqual(self.app.screen.pressed, [],
                         "the arm must not survive leaving the screen")

    def test_non_confirm_button_acts_immediately(self):
        self.app.press("safe")
        self.assertEqual(self.app.screen.pressed, ["safe"])


class DeclaredSurface(unittest.TestCase):
    """What the real screens actually expose."""

    def setUp(self):
        self.classes = list(SCREENS)

    # Buttons that are unconditionally destructive: confirm must be literal.
    ALWAYS_CONFIRM = {"reboot", "shutdown", "poweroff", "restart_ui",
                      "provision", "gnss"}
    # Buttons that TOGGLE a destructive state. Only the direction that STARTS
    # it may be confirmed — gating the stop would delay killing RF or halting
    # a capture, which makes the guard actively harmful.
    CONDITIONAL_CONFIRM = {"tx", "radio", "power", "stop"}

    def test_destructive_actions_are_confirm_guarded(self):
        """The list §10 promises is guarded. A toggle may compute `confirm`
        rather than hardcode it, but it must not be flatly False."""
        import inspect
        for cls in self.classes:
            fn = cls.__dict__.get("model_buttons")
            if fn is None:
                continue
            src = inspect.getsource(fn)
            for line in src.splitlines():
                for bid in self.ALWAYS_CONFIRM | self.CONDITIONAL_CONFIRM:
                    if f'"id": "{bid}"' not in line:
                        continue
                    at = src.index(line)
                    block = src[at:at + 400]
                    with self.subTest(screen=cls.__name__, button=bid):
                        self.assertIn('"confirm"', block,
                                      f"{cls.__name__} button {bid!r} declares "
                                      f"no confirm at all")
                        if bid in self.ALWAYS_CONFIRM:
                            self.assertIn('"confirm": True', block,
                                          f"{cls.__name__} button {bid!r} is "
                                          f"unconditionally destructive and "
                                          f"must be confirmed outright")
                        else:
                            self.assertNotIn('"confirm": False', block,
                                             f"{cls.__name__} button {bid!r} "
                                             f"toggles a destructive state and "
                                             f"must confirm the start direction")

    def test_tx_confirms_starting_but_not_stopping(self):
        """Pinned explicitly because it is the sharpest edge in §10: keying a
        transmitter needs a second press, killing it must not."""
        import inspect
        from kilodash.screens.aiscatcher import AisCatcherScreen
        src = inspect.getsource(AisCatcherScreen.model_buttons)
        self.assertIn('"confirm": not self.transmitting', src,
                      "TX must confirm on start and fire immediately on stop")

    def test_buttons_declare_the_required_fields(self):
        for cls in self.classes:
            fn = cls.__dict__.get("model_buttons")
            if fn is None:
                continue
            with self.subTest(screen=cls.__name__):
                import inspect
                src = inspect.getsource(fn)
                self.assertIn('"id"', src)
                self.assertIn('"enabled"', src)
                self.assertIn('"confirm"', src)


if __name__ == "__main__":
    unittest.main()
