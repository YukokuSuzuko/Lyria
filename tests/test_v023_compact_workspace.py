from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest
from test_qt_timeline_input import ui, timeline_video, application, find, click
from test_qt_razor_input import cut_at


def test_small_workspace_has_whole_review_rows_and_pending_budget(ui,tmp_path):
    app,b,w,t=ui
    w.resize(760,600);QTest.qWait(300)
    review=find(w,'reviewPanel');rows=find(w,'reviewList');tracks=find(w,'timelineTracks')
    assert rows.height()>=74
    assert tracks.height()>=30
    before=(review.height(),review.y(),t.height(),t.y(),review.property('minimumPanelHeight'))
    assert w.grabWindow().save(str(tmp_path/'compact-before.png'))
    click(w,find(w,'timelineRazorTool'));cut_at(w,t,'r40',424)
    QTest.qWait(100)
    assert find(w,'reviewPendingText').isVisible()
    assert rows.height()>=38
    assert (review.height(),review.y(),t.height(),t.y(),review.property('minimumPanelHeight'))==before
    for name in ('confirmCurrent','reviewAllocateSplitText','reviewKeepSplitText','transportPlayPause','timelineHorizontalScroll'):
        item=find(w,name);point=item.mapToScene(QPointF())
        assert item.isVisible() and 0<=point.y() and point.y()+item.height()<=w.height()
    assert w.grabWindow().save(str(tmp_path/'compact-pending.png'))
