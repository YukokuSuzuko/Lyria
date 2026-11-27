"""Shared application icon, also present in the frozen assets directory."""
from pathlib import Path


def application_icon_path():
    return Path(__file__).resolve().parent / "assets" / "app" / "lyria.ico"


def setup_application_icon(application):
    from PySide6.QtGui import QIcon
    icon = QIcon(str(application_icon_path()))
    application.setWindowIcon(icon)
    return icon
