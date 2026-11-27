"""Interaction regressions against the production OCR entry and its project transaction."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog
from lyricsync.model import Lyric, Mapping, Project, Translation, Visual
from lyricsync.ocr_source_pairing import source_links, source_text, suggest_source_links
from lyricsync.project_document import write_document, load_document
from test_qt_entry_initialization_dev5 import bridge
from test_v029_source_pairing import launch

def sample():
    lyrics=[Lyric(f'l{i}', '同文' if i in (3,4) else f'行{i}', None,None) for i in range(1,14)]
    trans=[Translation(f't{i}',f'译文{i}') for i in range(1,12)]
    vis=[Visual(f'v{i}',f'参考{i}',float(i),i+.8,[0,0,50,12],.96,12) for i in range(1,12)]
    cues=[Mapping(f'm{i}',[f't{i}'],visual_ids=[f'v{i}'],start=float(i),end=i+.8,method='candidate_ocr') for i in range(1,12)]
    return Project(lyrics=lyrics,translations=trans,visuals=vis,mappings=cues)

def click(pool,i,mod=Qt.KeyboardModifier.NoModifier):
    pool.scrollToItem(pool.item(i))
    QApplication.processEvents()
    QTest.mouseClick(pool.viewport(),Qt.MouseButton.LeftButton,mod,pool.visualItemRect(pool.item(i)).center())

def test_keyboard_range_group_and_project_transaction(bridge,monkeypatch,tmp_path):
    original=sample()
    d=launch(bridge,original)
    QApplication.processEvents()
    try:
        click(d.pool,0)
        click(d.pool,2,Qt.KeyboardModifier.ControlModifier)
        assert d.pool.selected_ids()==['l1','l3']
        d.table.setCurrentCell(1,1)
        click(d.pool,2)
        click(d.pool,3,Qt.KeyboardModifier.ShiftModifier)
        assert d.pool.selected_ids()==['l3','l4']
        QTest.keyClick(d.pool,Qt.Key.Key_Enter)
        assert source_links(d.draft)['t2']==['l3','l4'] and d.target()=='t3'
        monkeypatch.setattr(QInputDialog,'getMultiLineText',lambda *_:('第二条修订',True))
        d.change_member('t2','l4','edit')
        assert [source_text(d.draft,d.draft.lyrics[i]) for i in (2,3)]==['同文','第二条修订']
        d.change_member('t2','l4','unpair')
        assert source_links(d.draft)['t2']=='l3'
        d.history(False)
        assert source_links(d.draft)['t2']==['l3','l4']
        d.table.setCurrentCell(0,1)
        click(d.pool,0)
        for i in range(10):
            QTest.keyClick(d.pool,Qt.Key.Key_Return)
        assert len(source_links(d.draft))==11
        assert not source_links(bridge.document.project)
        QTest.keyClick(d.apply_button,Qt.Key.Key_Return)
        assert d.isVisible() and not source_links(bridge.document.project)
        d.apply_button.click()
        assert not d.isVisible() and len(source_links(bridge.document.project))==11
        path=tmp_path/'paired.lyria'
        write_document(bridge.document.snapshot(explicit_save=True),path)
        recovered=load_document(path).project
        assert source_links(recovered)['t2']==['l3','l4']
        assert source_text(recovered,recovered.lyrics[3])=='第二条修订'
        for field in ('lyrics','translations','visuals','mappings','alignment_graph'):
            assert recovered.to_dict()[field]==original.to_dict()[field]
        bridge._history(False)
        assert not source_links(bridge.document.project)
    finally:
        d.close()

def test_ordered_drop_targets_translation_only_and_cancel(bridge):
    d=launch(bridge,sample())
    QApplication.processEvents()
    try:
        pool=d.pool
        click(pool,2)
        click(pool,3,Qt.KeyboardModifier.ShiftModifier)
        start=pool.visualItemRect(pool.item(3)).center()
        def drop(col):
            dest=d.table.visualItemRect(d.table.item(1,col)).center()
            QTest.mousePress(pool.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
            QTest.mouseMove(d.table.viewport(),dest)
            QTest.mouseRelease(d.table.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,dest)
        drop(0)
        assert not source_links(d.draft)
        click(pool,2)
        click(pool,3,Qt.KeyboardModifier.ShiftModifier)
        drop(1)
        assert source_links(d.draft)=={'t2':['l3','l4']}
        d.reject()
        assert not source_links(bridge.document.project)
    finally:
        d.close()

def test_auto_fill_skips_blank_duplicate_and_low_confidence_and_keeps_manual():
    from lyricsync.ocr_source_pairing import edit_source_pair
    p=sample()
    p=edit_source_pair(p,'pair',translation_id='t1',source_ids=['l1'])
    p=edit_source_pair(p,'edit',source_id='l1',text='人工修订')
    p.visuals[1].text='同文'
    p.visuals[2].text='行5行6'
    p.visuals[3].text='行7'
    p.visuals[3].confidence=.62
    p.visuals[4].text='行8'
    p.mappings[5].translation_ids=[]
    p.visuals[6].text='行10'
    before=deepcopy(p.to_dict())
    after,hints=suggest_source_links(p)
    assert source_links(after)=={'t1':'l1','t3':['l5','l6'],'t5':'l8','t7':'l10'}
    assert {'t2','t4'}<=hints.keys()
    assert p.to_dict()==before and source_text(after,after.lyrics[0])=='人工修订'


def test_real_bridge_action_and_close_guard(bridge):
    from PySide6.QtCore import QTimer
    from desktop_qt.ocr_source_pairing_dialog import OcrSourcePairingDialog
    bridge.command('project.newEntry', {'mode':'ocr'})
    assert bridge._adopt_project(sample())
    bridge._changed()
    receipts = []
    def inspect():
        d = QApplication.activeModalWidget()
        try:
            assert isinstance(d, OcrSourcePairingDialog)
            click(d.pool,0)
            QTest.keyClick(d.pool,Qt.Key.Key_Return)
            d.close()
            assert d.isVisible() and d.discard.isVisible()
            d.back_to_draft.click()
            assert source_links(d.draft) and not source_links(bridge.document.project)
            receipts.append(True)
        finally:
            if d: d.reject()
    QTimer.singleShot(75,inspect)
    assert bridge.command('pairing.workbench',{})
    assert receipts == [True] and not source_links(bridge.document.project)


def test_auto_crossings_are_suggestions_and_exact_groups_can_exceed_four():
    p=sample()
    p.visuals[0].text='行2'
    p.visuals[1].text='行1'
    p.visuals[2].text='行5行6行7行8行9'
    out,hints=suggest_source_links(p)
    assert source_links(out)=={'t3':['l5','l6','l7','l8','l9']}
    assert {'t1','t2'}<=hints.keys()


def test_actual_qt_drag_selected_group_to_middle(bridge):
    import os
    from PySide6.QtCore import QTimer,QPoint
    d=launch(bridge,sample())
    QApplication.processEvents()
    pool,table=d.pool,d.table
    click(pool,2)
    click(pool,3,Qt.KeyboardModifier.ShiftModifier)
    assert pool.selected_ids()==['l3','l4']
    start=pool.visualItemRect(pool.item(3)).center()
    dest=table.visualItemRect(table.item(1,1)).center()
    timers=[]
    def later(ms,callback):
        timer=QTimer(d);timer.setSingleShot(True);timer.timeout.connect(callback);timer.start(ms);timers.append(timer)
    try:
        QTest.mousePress(pool.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        later(70,lambda:QTest.mouseMove(table.viewport(),dest))
        later(140,lambda:QTest.mouseRelease(table.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,dest))
        later(650,lambda:QTest.keyClick(pool,Qt.Key.Key_Escape))
        QTest.mouseMove(pool.viewport(),start+QPoint(0,22))
        QTest.mouseMove(pool.viewport(),start+QPoint(0,38))
        QTest.qWait(220)
        assert source_links(d.draft)=={'t2':['l3','l4']}
        if os.getenv('LYRIA_DESK_PICTURE'):
            assert d.grab().save(os.environ['LYRIA_DESK_PICTURE'])
    finally:
        for timer in timers:timer.stop()
        d.reject()


def test_shift_skips_hidden_l4_and_drop_uses_pointer_target(bridge):
    from lyricsync.ocr_source_pairing import edit_source_pair
    p=edit_source_pair(sample(),'pair',translation_id='t3',source_id='l4')
    d=launch(bridge,p)
    try:
        d.hide_used.click()
        assert d.pool.item(3).isHidden() and d.target()=='t1'
        click(d.pool,2)
        click(d.pool,4,Qt.KeyboardModifier.ShiftModifier)
        assert d.pool.selected_ids()==['l3','l5']
        start=d.pool.visualItemRect(d.pool.item(4)).center()
        dest=d.table.visualItemRect(d.table.item(1,1)).center()
        QTest.mousePress(d.pool.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        QTest.mouseMove(d.table.viewport(),dest)
        QTest.mouseRelease(d.table.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,dest)
        assert source_links(d.draft)=={'t2':['l3','l5'],'t3':'l4'}
        assert source_links(bridge.document.project)=={'t3':'l4'}
    finally:
        d.reject()


def test_repeated_text_resolved_only_inside_manual_anchors():
    from lyricsync.ocr_source_pairing import edit_source_pair
    p=sample()
    p.lyrics=[Lyric(f's{i}',word,None,None) for i,word in enumerate(['もう一度','合図','もう一度','ひかり'],1)]
    for i,word in enumerate(['もう一度','合図','ひかり']):
        p.visuals[i].text=word
    p=edit_source_pair(p,'pair',translation_id='t2',source_id='s2')
    matched,hints=suggest_source_links(p)
    assert source_links(matched)=={'t1':'s1','t2':'s2','t3':'s4'}
    assert suggest_source_links(matched)[0].to_dict()==matched.to_dict()
    crossed=edit_source_pair(p,'pair',translation_id='t1',source_id='s3')
    preserved,hints=suggest_source_links(crossed)
    assert preserved.to_dict()==crossed.to_dict() and 't3' in hints
