"""Experimental test-only single-interpreter root with fresh toplevel per case."""
import tkinter as tk
from unittest.mock import patch
root=None

def pytest_collection_modifyitems(items):
    global root
    classes={item.cls for item in items if getattr(item,'cls',None) is not None and item.cls.__name__=='PlaybackTests'}
    if not classes:return
    root=tk.Tk();root.withdraw()
    for cls in classes:
        original=cls.setUp
        def setup(self,_original=original):
            # Only test's constructor is substituted; application and assertions unchanged.
            with patch('tkinter.Tk',lambda:tk.Toplevel(root)):
                return _original(self)
        cls.setUp=setup

def pytest_sessionfinish(session,exitstatus):
    if root is not None:
        from lyricsync.tk_lifecycle import destroy_root
        destroy_root(root)
