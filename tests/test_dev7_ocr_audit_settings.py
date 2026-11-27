"""Settings persistence/rescan contracts. Synthetic video and stub OCR, no quality claim."""
import json
from unittest.mock import patch
import pytest
from lyricsync import audit_service, vision
from lyricsync.model import Detection
from test_qt_qc_sentences import sentence_fixture
import test_qc_migration_recheck as recheck_fixture


@pytest.mark.parametrize('strategy', ['legacy', 'sentence'])
def test_both_audit_report_types_persist_and_forward_explicit_ocr_profile(sentence_fixture, tmp_path, monkeypatch, strategy):
    video, reference, roi = sentence_fixture
    reference.write_text('1\n00:00:00,200 --> 00:00:01,200\nHello\n', encoding='utf-8')
    calls = []
    class OCR:
        signature = {'name':'controlled-test'}
        def recognize(self, image):
            return [Detection('Hello', [1, 1, 100, 30], .99)]
    def factory(options):
        calls.append(dict(options))
        return OCR()
    monkeypatch.setattr('lyricsync.audit_sentences.make_ocr', factory)
    monkeypatch.setattr(audit_service, 'make_ocr', factory)
    monkeypatch.setattr(audit_service, 'scan', lambda *a, **k: ([], {}))
    output = tmp_path / strategy
    selected = {'ocr_model':'ppocrv4', 'ocr_preprocess':'raw'}
    report = audit_service.run_audit(video, reference, output, chinese_roi=roi,
        source_roi={'box':[0,90,320,170], 'origin':'user', 'confirmed':True},
        settings={**selected, 'audit_strategy':strategy})
    assert len(calls) == 2
    for options in calls + [report['ocr_settings'], json.loads((output/'report.json').read_text('utf-8'))['ocr_settings']]:
        assert {k:options.get(k) for k in selected} == selected


@pytest.mark.parametrize('stored, override, expected', [
    ({'ocr_model':'ppocrv4', 'ocr_preprocess':'raw'}, {}, ('ppocrv4', 'raw')),
    ({'ocr_model':'ppocrv4', 'ocr_preprocess':'raw'}, {'ocr_model':'ppocrv6', 'ocr_preprocess':'subtitle'}, ('ppocrv6', 'subtitle')),
    ({}, {}, ('ppocrv6', 'subtitle')),
    (None, {}, ('ppocrv6', 'subtitle')),
])
def test_rescan_inherits_explicit_profile_and_old_reports_keep_default_compatibility(tmp_path, stored, override, expected):
    helper = recheck_fixture.RecheckTests()
    parent, output = helper.parent(tmp_path)
    if stored is None:
        parent.pop('ocr_settings')
    else:
        parent['ocr_settings'].update(stored)
    child = helper.child(parent)
    with patch.object(audit_service, 'Video', return_value=recheck_fixture.FakeVideo(recheck_fixture.clock().stamps)), patch.object(audit_service.AuditClock, 'build', return_value=recheck_fixture.clock()), patch.object(audit_service, 'run_audit', return_value=child) as run:
        audit_service.rescan_audit(parent, output, first_frame=2, last_frame=4, cue_ids=['c'], settings=override)
    transmitted = run.call_args.kwargs['settings']
    if stored:
        for key, value in {**stored, **override}.items():
            assert transmitted[key] == value
    with patch.object(vision, 'RapidOCRProvider') as provider:
        vision.make_ocr(transmitted)
    assert (provider.call_args.kwargs['model'], provider.call_args.kwargs['preprocess']) == expected
    if stored:
        persisted = json.loads((output/'report.json').read_text('utf-8'))
        assert all(persisted['ocr_settings'][key] == value for key, value in stored.items())
