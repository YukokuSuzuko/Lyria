"""Offscreen reading button -> real external worker -> UI fill diagnostic."""
import argparse
import json
import os
from pathlib import Path
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
from desktop_qt.local_model_tasks import model_tasks
from lyricsync.model import Project,Lyric
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import NEXTFIRE


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    app=QApplication([])
    original=ProjectDocument(Project(lyrics=[Lyric('first','桜',None,None,reading='my-sakura'),
        Lyric('second','雪',None,None),Lyric('third','桜',None,None)]))
    before=original.snapshot()
    dialog=LocalAlignmentDialog(original,initial_options=dict(runtime_python=args.runtime,
        cache_root=str(args.out/'empty-model-cache'),model_id=NEXTFIRE.id,device='cuda:0'))
    started=time.monotonic();dialog.reading_button.click()
    pause_hidden=dialog.pause_task.isHidden()
    deadline=started+30
    while dialog._task is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    if dialog._task is not None:
        dialog._cancel_task();model_tasks().shutdown();app.processEvents()
    rows=dialog._current_readings()
    error=model_tasks().error
    successful=not error and all(row['reading'] for row in rows)
    data=dict(success=successful,task_finished=dialog._task is None,seconds=round(time.monotonic()-started,3),
        error=error,device_key_not_required=True,pause_download_hidden=pause_hidden,
        prior_reading_preserved=rows[0]['reading']=='my-sakura',
        row_ids_preserved=[row['id'] for row in rows]==['reference-first','reference-second','reference-third'],
        generated_readings=[row['reading'] for row in rows],
        document_unchanged=original.snapshot()==before,foreground_used=False,acoustic_inference=False,
        missing_dependency_repair_visible=not dialog.reading_repair.isHidden())
    (args.out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False))
    dialog.reject();dialog.deleteLater();service=model_tasks();service.shutdown();service.deleteLater()
    delattr(app,'_lyria_model_tasks');QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    return 0 if successful and pause_hidden and data['prior_reading_preserved'] else 1


if __name__=='__main__':raise SystemExit(main())
