import json
from desktop_qt.controller import Bridge
from test_qt_timeline_commands import application, bridge


def test_workspace_layout_persists_without_document_or_history(bridge,application):
    before=bridge.document.snapshot()
    desired={**bridge.state['workspaceLayout'],'previewFraction':.63,'topFraction':.72,'previewHeight':500.,'audioHeightExplicit':True}
    assert bridge.command('view.layout',desired)
    assert bridge.document.snapshot()==before and not bridge.history.undo_stack
    assert bridge.state['workspaceLayout']==desired
    other=Bridge(storage_dir=bridge.storage_dir)
    try:assert other.state['workspaceLayout']==desired
    finally:other.shutdown();other.wait_for_shutdown(application)
    assert json.loads((bridge.storage_dir/'workspace-layout.json').read_text())==desired


def test_workspace_layout_invalid_values_cannot_pollute_saved_preferences(bridge):
    before=dict(bridge.state['workspaceLayout'])
    for payload in ({'path':'elsewhere'},{'previewFraction':float('nan')},{'topFraction':True}):
        assert not bridge.command('view.layout',payload)
        assert bridge.state['workspaceLayout']==before
