"""Edition-scoped storage independent of the current working directory."""
from pathlib import Path
import sys

def application_name():
    from lyricsync import __version__, __edition__
    return "Lyria-" + __version__ + ("-ocr-modified" if __edition__ else "-main")

def default_portable_storage():
    """Frozen editions keep their profile next to the delivery, never in it."""
    if not getattr(sys, "frozen", False):
        return None
    return str(Path(sys.executable).absolute().parent.parent / "profiles" / application_name())

def portable_preferences(value):
    """Do not persist the discovered bundle's movable absolute interpreter path."""
    from lyricsync.shared_runtime import discover_shared_runtime
    result = dict(value)
    try:
        shared = discover_shared_runtime()
        if shared is not None:
            for key in ("runtime_python", "reading_runtime_python"):
                setting = result.get(key)
                if setting and Path(setting).expanduser().resolve() == Path(shared).resolve():
                    result[key] = ""
    except (OSError, ValueError):
        # An explicitly selected external runtime is preserved, even when offline.
        pass
    return result
