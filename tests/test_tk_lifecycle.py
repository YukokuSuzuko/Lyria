"""Real Tk regression for callback ownership during complete teardown."""
import tkinter as tk
import unittest

from lyricsync.tk_lifecycle import destroy_root


class TkLifecycleTests(unittest.TestCase):
    def test_child_owned_timer_is_unregistered_only_by_its_widget(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        child = tk.Canvas(root)
        calls = []
        child.after(1000, lambda: calls.append('child'))
        root.after(1000, lambda: calls.append('root'))
        try:
            destroy_root(root)
            self.assertEqual(calls, [])
            self.assertFalse(root.tk.call('after', 'info'))
            self.assertIsNone(child._tclCommands)
        finally:
            try:
                root.tk.call('destroy', '.')
            except tk.TclError:
                pass
