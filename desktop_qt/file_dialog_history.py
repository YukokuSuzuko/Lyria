"""Local file-picker directories; never restore a selected file or upload paths."""
from pathlib import Path
import json
import os

class FileDialogHistory:
    def __init__(self,storage_dir):
        self.path=Path(storage_dir)/'file-dialog-folders.json'
        try:
            value=json.loads(self.path.read_text(encoding='utf-8'))
            self.folders={k:v for k,v in value.items() if k in ('open','save','import') and isinstance(v,str)}
        except (OSError,ValueError,AttributeError):self.folders={}

    @staticmethod
    def accessible(path):
        try:return path.is_dir() and os.access(path,os.R_OK|os.X_OK)
        except OSError:return False

    def initial(self,category,suggested=''):
        raw=self.folders.get(category,'')
        directory=Path(raw) if raw else None
        if not directory or not self.accessible(directory):
            directory=Path.home()
        if not self.accessible(directory):return suggested
        if suggested:
            target=Path(suggested)
            if target.is_absolute():return str(target)
            return str(directory/target.name)
        return str(directory)

    def remember(self,category,selection):
        if category not in ('open','save','import'):return
        parent=Path(selection).expanduser().resolve().parent
        if not self.accessible(parent):return
        self.folders[category]=str(parent)
        try:
            from lyricsync.storage import atomic_json
            atomic_json(self.path,self.folders)
        except OSError:pass  # A preference failure must not cancel a valid import.
