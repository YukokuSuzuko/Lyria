"""Dialog preference persistence cannot invalidate a just-confirmed ASR draft."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from desktop_qt.controller import Bridge
from lyricsync.local_models import ASR
from lyricsync.local_transcription import confirm_transcript_draft


@pytest.mark.parametrize('intervening_edit',[False,True])
def test_source_save_rebases_only_own_revision(tmp_path,monkeypatch,intervening_edit):
    app=QApplication.instance() or QApplication([])
    b=Bridge(storage_dir=tmp_path/'storage')
    original_revision=b.document.revision
    draft={'kind':'transcript_draft','status':'confirmed','model_id':ASR.id,'model_revision':ASR.revision,
           'document_id':b.document.id,'revision':original_revision,'complete':True,
           'id':'draft-test','corrected_text':'One line'}
    class Dialog:
        def __init__(self,*args,**kwargs):pass
        def exec(self):
            if intervening_edit:b.document.touch()
            return QDialog.DialogCode.Accepted
        def options(self):return {'transcript_draft':deepcopy(draft)}
        def source_state(self):return {'version':1,'text_source':'external','origin':'clipboard',
                                      'external_rows':[{'id':'external-1','text':'One line'}], 'reading_rows':[]}
    monkeypatch.setattr('desktop_qt.local_alignment_dialog.LocalAlignmentDialog',Dialog)
    checked=[]
    def generate(document,index,*,options,**kwargs):
        checked.append(confirm_transcript_draft(options['transcript_draft'],'One line',document,confirmed=True))
    monkeypatch.setattr('lyricsync.local_transcription.generate_confirmed_draft_candidate',generate)
    monkeypatch.setattr(b,'_start_candidate_job',lambda kind,source,work,callback:work(lambda:False,lambda *a:None))
    try:
        if intervening_edit:
            with pytest.raises(ValueError,match='已失效'):b._configure_vocal()
            assert not checked
        else:
            b._configure_vocal()
            assert checked[0]['revision']==b.document.revision==original_revision+1
        assert b.document.project.settings['local_alignment_source']['origin']=='clipboard'
    finally:
        b.shutdown();b.wait_for_shutdown(app)
