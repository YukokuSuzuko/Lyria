from desktop_qt.workspace_layout import DEFAULTS, update_layout


def test_legacy_height_is_explicit_and_reset_restores_auto():
    old=update_layout(DEFAULTS,{'audioHeight':200},loading=True)
    assert old['audioHeight']==200 and old['audioHeightExplicit']
    saved=update_layout(DEFAULTS,dict(DEFAULTS),loading=True)
    assert not saved['audioHeightExplicit']
    manual=update_layout(saved,{'audioHeight':210})
    assert manual['audioHeight']==210 and manual['audioHeightExplicit']
    assert not update_layout(manual,{'reset':True})['audioHeightExplicit']
