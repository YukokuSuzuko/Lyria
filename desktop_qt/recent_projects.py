"""Local recent-project metadata, never document content or credentials."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path

LIMIT = 8

class RecentProjects:
    def __init__(self, storage_dir):
        self.path = Path(storage_dir) / 'recent-projects.json'
        self.items = []
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, list):
                seen = set()
                for entry in data:
                    if not isinstance(entry, dict): continue
                    path = entry.get('path', '')
                    if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.casefold() != '.lyria': continue
                    key = os.path.normcase(os.path.normpath(path))
                    if key in seen: continue
                    seen.add(key)
                    self.items.append({'path': path, 'name': Path(path).stem,
                                       'openedAt': str(entry.get('openedAt', ''))[:40]})
                    if len(self.items) == LIMIT: break
        except (OSError, ValueError, TypeError): pass

    def snapshot(self):
        result = []
        for entry in self.items:
            try: available = Path(entry['path']).is_file()
            except OSError: available = False
            result.append({**entry, 'available': available})
        return result

    def remember(self, path):
        path = str(Path(path).absolute())
        if Path(path).suffix.casefold() != '.lyria': return
        key = os.path.normcase(os.path.normpath(path))
        self.items = [entry for entry in self.items if os.path.normcase(os.path.normpath(entry['path'])) != key]
        self.items.insert(0, {'path': path, 'name': Path(path).stem,
                             'openedAt': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        self.items = self.items[:LIMIT]
        self._save()

    def forget(self, path):
        self.items = [entry for entry in self.items if entry['path'] != path]
        self._save()

    def _save(self):
        # Failure to persist UI history must never fail a project save/open.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
        except OSError: pass
