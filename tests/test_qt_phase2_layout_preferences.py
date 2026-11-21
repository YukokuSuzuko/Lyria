import json
import pytest
from desktop_qt.controller import Bridge
from test_qt_timeline_commands import application, bridge


def test_four_panel_layout_persistence_reset_and_no_document_edits(bridge):
    before = bridge.document.snapshot()
    assert bridge.command('view.layout', {'audioHeight': 220, 'audioMode': 'spectrum', 'audioVisible': False, 'spectrumPreset': 'balanced', 'spectrumWindow': 1024, 'spectrumGain': 6})
    assert bridge.state['workspaceLayout']['audioHeight'] == 220
    assert bridge.state['workspaceLayout']['audioMode'] == 'spectrum'
    assert bridge.state['workspaceLayout']['audioVisible'] is False
    assert bridge.state['workspaceLayout']['spectrumPreset'] == 'balanced'
    assert bridge.state['workspaceLayout']['spectrumWindow'] == 1024
    assert bridge.state['workspaceLayout']['spectrumGain'] == 6
    other = Bridge(storage_dir=bridge.storage_dir)
    try:
        assert other.state['workspaceLayout'] == bridge.state['workspaceLayout']
    finally:
        other.shutdown()
    assert bridge.command('view.layout', {'reset': True})
    assert bridge.state['workspaceLayout']['audioHeight'] == 160
    assert bridge.state['workspaceLayout']['audioMode'] == 'waveform'
    assert bridge.state['workspaceLayout']['audioVisible'] is True
    assert bridge.state['workspaceLayout']['spectrumPreset'] == 'detail'
    assert bridge.state['workspaceLayout']['spectrumWindow'] == 384
    assert bridge.state['workspaceLayout']['spectrumGain'] == 0
    assert bridge.document.snapshot() == before
    assert not bridge.history.undo_stack


@pytest.mark.parametrize('payload', [{'audioMode': 'fake'}, {'audioHeight': True},
                                     {'audioHeight': float('nan')}, {'reset': 'yes'},
                                     {'reset': True, 'audioMode': 'fake'}, {'audioVisible': 1},
                                     {'spectrumPreset': 'auto'}, {'spectrumWindow': 255},
                                     {'spectrumWindow': True}, {'spectrumGain': float('nan')},
                                     {'spectrumGain': True}])
def test_invalid_layout_is_atomic(bridge, payload):
    assert bridge.command('view.layout', {'audioHeight': 230, 'audioMode': 'spectrum'})
    before = bridge.state['workspaceLayout'].copy()
    disk = (bridge.storage_dir/'workspace-layout.json').read_bytes()
    assert not bridge.command('view.layout', payload)
    assert bridge.state['workspaceLayout'] == before
    assert (bridge.storage_dir/'workspace-layout.json').read_bytes() == disk


def test_old_and_corrupt_layout_settings_remain_safe(bridge):
    for saved in ({'previewFraction': .61, 'previewHeight': 470},
                  {'audioHeight': -5, 'audioMode': 'bad', 'previewFraction': .61},
                  None, ['bad']):
        (bridge.storage_dir/'workspace-layout.json').write_text(json.dumps(saved))
        other = Bridge(storage_dir=bridge.storage_dir)
        try:
            assert other.state['workspaceLayout']['audioHeight'] == 160
            assert other.state['workspaceLayout']['audioMode'] == 'waveform'
            if isinstance(saved, dict):
                assert other.state['workspaceLayout']['previewFraction'] == .61
        finally:
            other.shutdown()


@pytest.mark.parametrize('window',[256,384])
def test_short_spectrum_windows_are_valid_without_document_mutation(bridge,window):
    before=bridge.document.snapshot()
    assert bridge.command('view.layout',{'spectrumWindow':window})
    assert bridge.state['workspaceLayout']['spectrumWindow']==window
    assert bridge.document.snapshot()==before
