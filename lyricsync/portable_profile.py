"""Edition-scoped storage independent of the current working directory."""
import os
from pathlib import Path
import sys

def application_name():
    from lyricsync import __version__, __edition__
    return "Lyria-" + __version__ + ("-test" if __edition__ == "测试版" else "-ocr-modified" if __edition__ else "-main")

def package_root():
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).absolute().parent.parent

def is_museum_archive(install=None):
    base = Path(install) if install is not None else package_root()
    if base is None:
        return False
    museum = base.parent
    return (museum / "LYRIA_MUSEUM_ARCHIVE.json").is_file() or (museum / "evidence" / "archive-receipt.json").is_file()

def external_museum_data_dir(install=None):
    base = Path(install) if install is not None else package_root()
    drive_root = Path(base.drive + "/") if base and base.drive else Path.home()
    return drive_root / "Lyria运行数据" / application_name()

def profiles_root(install=None):
    override = os.environ.get("LYRIA_PROFILES_DIR", "").strip()
    if override:
        return Path(override)
    base = Path(install) if install is not None else package_root()
    if base is None:
        return None
    return base / "profiles"

def default_portable_storage():
    """Frozen editions keep their profile next to the delivery, never in it."""
    if not getattr(sys, "frozen", False):
        return None
    override = os.environ.get("LYRIA_STORAGE_DIR", "").strip()
    if override:
        return override
    return str(profiles_root() / application_name())

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
