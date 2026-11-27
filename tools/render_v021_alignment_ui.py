import os,sys
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from desktop_qt.local_alignment_dialog import LocalAlignmentDialog
from lyricsync.model import Project,Lyric
from lyricsync.project_document import ProjectDocument
from lyricsync.local_models import NEXTFIRE
from pathlib import Path
from PySide6.QtGui import QFont,QFontDatabase
app=QApplication([]);QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc');app.setFont(QFont('Microsoft YaHei',9));d=LocalAlignmentDialog(ProjectDocument(Project(lyrics=[Lyric('a','桜',None,None)])),initial_options={'model_id':NEXTFIRE.id,'cache_root':str(Path('../validation/ui-v021-render/cache').resolve())})
d.show();app.processEvents();Path('../validation/ui-v021-render').mkdir(parents=True,exist_ok=True);d.grab().save('../validation/ui-v021-render/alignment.png');d.reject()
