from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_dev9_readings_dialog import dialog
from test_qt_local_alignment_phase4 import application

def test_open_source_menu_keeps_warning_and_keyboard(application,tmp_path):
    d=dialog(tmp_path)
    try:
        d.show()
        d.tabs.setCurrentWidget(d.settings_scroll)
        QTest.qWait(150)
        combo=d.text_source
        assert combo.isVisible() and combo.isEnabled()
        combo.setMinimumWidth(0);combo.setFixedWidth(135)
        combo.setFocus()
        combo.showPopup()
        import time
        end=time.monotonic()+1.5
        while not combo.view().isVisible() and time.monotonic()<end:
            QTest.qWait(20)
        popup_opened = combo.view().isVisible()
        if not popup_opened:
            print('Popup evidence:',[(type(w).__name__,w.objectName(),w.isVisible(),w.width(),w.height())
                                     for w in QApplication.topLevelWidgets()])
            print('combo geometry:',combo.geometry(),combo.view().geometry(),combo.view().window().geometry(),
                  combo.screen().availableGeometry())

        text=combo.itemText(combo.findData('main'))
        assert '可能是译文' in text
        assert combo.view().width() >= combo.fontMetrics().horizontalAdvance(text)+6
        assert popup_opened, (combo.isVisible(),combo.isEnabled(),QApplication.platformName(),combo.count())
        QTest.keyClick(combo.view(),Qt.Key.Key_End)
        QTest.keyClick(combo.view(),Qt.Key.Key_Return)
        assert combo.currentData()=='main' and not combo.view().isVisible()
    finally:
        d.reject()
