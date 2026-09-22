"""A menu snapshot's window ID is not an action responder selection."""
import unittest
from unittest.mock import patch

from macws_menu_probe import require_focused_target, require_live_target


class MenuProbeFocusTests(unittest.TestCase):
    def test_exact_focus_required(self):
        for owner, focused, allowed in ((9, [17], True), (9, [18], False),
                                         (9, [], False), (9, [17, 18], False),
                                         (8, [17], False)):
            result = {"owner_pid_from_filename": owner,
                      "windows": [{"window_id": w, "flags": ["focused"]}
                                  for w in focused]}
            with self.subTest(owner=owner, focused=focused), patch(
                    "macws_menu_probe.read_metrics", return_value=result), patch(
                    "macws_menu_probe.os.kill"):
                if allowed:
                    require_focused_target(9, 17)
                else:
                    with self.assertRaises(RuntimeError):
                        require_focused_target(9, 17)

    def test_missing_metrics_does_not_allow_action(self):
        with patch("macws_menu_probe.read_metrics", side_effect=FileNotFoundError), \
                patch("macws_menu_probe.os.kill"):
            with self.assertRaises(FileNotFoundError):
                require_focused_target(9, 17)

    def test_dead_target_cannot_be_authorized_by_stale_focused_metrics(self):
        with patch('macws_menu_probe.os.kill', side_effect=ProcessLookupError), \
                patch('macws_menu_probe.read_metrics') as metrics:
            with self.assertRaises(RuntimeError):
                require_focused_target(9, 17)
            metrics.assert_not_called()

    def test_system_or_unobservable_pid_rejected(self):
        for pid in (-1, 0, 1):
            with self.assertRaises(RuntimeError):
                require_live_target(pid)
        with patch('macws_menu_probe.os.kill', side_effect=PermissionError):
            with self.assertRaises(RuntimeError):
                require_live_target(9)


if __name__ == "__main__":
    unittest.main()
