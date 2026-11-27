"""Text grouping contracts; synthetic inputs are not song-quality acceptance."""
from copy import deepcopy

from lyricsync.matching import associate_visuals, match_project
from lyricsync.model import Lyric, Mapping, Project, Translation, Visual


def sample(source, target, references=None):
    lyrics = [Lyric(f'l{i}', text, None, None) for i, text in enumerate(source)]
    visuals = [Visual(f'v{i}', text, i * 3., i * 3. + 2., [], .99, 4)
               for i, text in enumerate(source)]
    translations = [Translation(f't{i}', text,
                               reference=references[i] if references else '')
                    for i, text in enumerate(target)]
    return Project(lyrics=lyrics, translations=translations, visuals=visuals)


def by_translation(project):
    return {tid: row for row in project.mappings for tid in row.translation_ids}


def test_split_then_missing_short_line_does_not_shift_middle_despite_equal_counts():
    # The extra OCR split and absent interjection cancel the count difference.
    p = sample(['opening', 'follow the', 'long road', 'middle stanza', 'ending'],
               ['opening', 'follow the long road', 'middle stanza', 'oh', 'ending'])
    before = deepcopy(p.visuals)
    match_project(p)
    rows = by_translation(p)
    assert rows['t1'].lyric_ids == ['l1', 'l2']
    assert rows['t2'].lyric_ids == ['l3']
    assert rows['t3'].lyric_ids == [] and rows['t3'].start is None
    assert rows['t4'].lyric_ids == ['l4']
    assert p.visuals == before  # Grouping must not rewrite detection boundaries.


def test_partial_bilingual_references_are_not_ignored():
    p = sample(['opening', 'follow the', 'long road', 'middle stanza', 'ending'],
               ['译文甲', '译文乙', '译文丙', '呀', '译文丁'],
               ['opening', 'follow the long road', 'middle stanza', '', 'ending'])
    match_project(p)
    rows = by_translation(p)
    assert rows['t1'].lyric_ids == ['l1', 'l2']
    assert rows['t2'].lyric_ids == ['l3']
    assert rows['t3'].start is None
    assert rows['t4'].lyric_ids == ['l4']


def test_no_language_evidence_discloses_uncertainty_even_when_counts_cancel():
    p = sample(['初めの歌', '途中の歌', '最後の歌'], ['中文第一句', '中文中间句', '中文结尾句'])
    match_project(p)
    assert all(any('没有跨语言句意依据' in note for note in row.reasons) for row in p.mappings)
    assert not any(row.confirmed for row in p.mappings)


def test_protected_user_rows_and_visual_overlap_survive():
    p = sample(['opening', 'follow the', 'long road', 'ending'],
               ['opening', 'follow the long road', 'ending'])
    p.visuals[2].start = p.visuals[1].end - .5
    manual = Mapping('manual', ['t1'], ['l1'], ['v1'], 1., 20.,
                     locked=True, human_edited=True)
    p.mappings = [manual]
    before = deepcopy(p.visuals)
    match_project(p)
    assert next(row for row in p.mappings if row.id == 'manual') is manual
    assert (manual.start, manual.end, manual.visual_ids) == (1., 20., ['v1'])
    assert p.visuals == before


def test_short_ocr_fragments_require_complete_joined_text_evidence():
    p = sample(['朝焼けを待つ'], ['朝焼けを待つ'])
    p.visuals = [Visual('a', '朝焼', 1., 2., [], .99, 3),
                 Visual('b', 'けを待つ', 2., 4., [], .99, 3)]
    match_project(p)
    assert p.mappings[0].visual_ids == ['a', 'b']
    assert (p.mappings[0].start, p.mappings[0].end) == (1., 4.)


def test_long_gap_and_repeated_joined_fragments_do_not_claim_exact_evidence():
    for repeated, gap in ((False, 30.), (True, 0.)):
        p = sample(['朝焼けを待つ'] * (2 if repeated else 1), ['朝焼けを待つ'])
        p.visuals = [Visual('a', '朝焼', 1., 2., [], .99, 3),
                     Visual('b', 'けを待つ', 2.+gap, 4.+gap, [], .99, 3)]
        associations, _ = associate_visuals(p.lyrics, p.visuals)
        assert not any(any('多个 OCR' in note for note in notes)
                       for group in associations.values() for _, _, notes in group)


def test_exact_group_preserves_different_source_and_target_split_positions():
    p = sample(['along the ri', 'ver we sing'], ['along the', 'river we sing'])
    match_project(p)
    assert len(p.mappings) == 1
    assert p.mappings[0].translation_ids == ['t0', 't1']
    assert p.mappings[0].lyric_ids == ['l0', 'l1']
    assert any('独立起止时间' in note for note in p.mappings[0].reasons)


def test_repeated_complete_lines_are_not_merged_to_manufacture_unique_anchor():
    p = sample(['repeated chorus', 'repeated chorus'], ['repeated chorus', 'repeated chorus'])
    match_project(p)
    assert len(p.mappings) == 2
    assert [row.translation_ids for row in p.mappings] == [['t0'], ['t1']]


def test_pipeline_path_uses_text_groups_with_detached_timed_ocr_source(tmp_path):
    from unittest.mock import patch
    from lyricsync.pipeline import run
    p = sample(['opening', 'follow the', 'long road', 'middle stanza', 'ending'],
               ['opening', 'follow the long road', 'middle stanza', 'oh', 'ending'])
    p.lyrics = []
    video = tmp_path/'video.mp4'
    video.write_bytes(b'synthetic fixture; scan is controlled')
    translation = tmp_path/'translation.txt'
    translation.write_text('\n'.join(t.text for t in p.translations), encoding='utf-8')
    p.inputs.update(video=str(video), translation=str(translation))
    with patch('lyricsync.pipeline.scan', return_value=(p.visuals, {'duration': 16.})):
        run(p, tmp_path/'project.json')
    assert p.lyrics == []
    assert [row.visual_ids for row in p.mappings] == [['v0'], ['v1', 'v2'], ['v3'], [], ['v4']]
    assert p.mappings[3].start is None


def test_timed_repeated_reference_disambiguates_complete_fragment_occurrences():
    lyrics = [Lyric('first', '朝焼けを待つ', 1., 4.),
              Lyric('second', '朝焼けを待つ', 10., 13.)]
    visuals = [Visual('a', '朝焼', 1., 2., [], .99, 3),
               Visual('b', 'けを待つ', 2., 4., [], .99, 3),
               Visual('c', '朝焼', 10., 11., [], .99, 3),
               Visual('d', 'けを待つ', 11., 13., [], .99, 3)]
    result, unknown = associate_visuals(lyrics, visuals)
    assert not unknown
    assert [v.id for v, _, _ in result['first']] == ['a', 'b']
    assert [v.id for v, _, _ in result['second']] == ['c', 'd']


def test_ambiguous_timed_reference_does_not_claim_fragment_occurrence():
    lyrics = [Lyric('first', '朝焼けを待つ', 1., 4.),
              Lyric('second', '朝焼けを待つ', 1., 4.)]
    visuals = [Visual('a', '朝焼', 1., 2., [], .99, 3),
               Visual('b', 'けを待つ', 2., 4., [], .99, 3)]
    result, unknown = associate_visuals(lyrics, visuals)
    assert not any(result.values())
    assert unknown == ['a', 'b']


def test_trailing_fragment_cannot_shift_reference_offset_for_unique_line():
    lyrics = [Lyric('one', '朝焼けを待つ', 1., 4.)]
    visuals = [Visual('a', '朝焼', 1., 2., [], .99, 3),
               Visual('b', 'けを待つ', 2., 4., [], .99, 3)]
    result, unknown = associate_visuals(lyrics, visuals)
    assert not unknown
    assert [v.id for v, _, _ in result['one']] == ['a', 'b']


def test_source_effect_styles_do_not_create_ambiguous_repeat_in_pipeline(tmp_path):
    """Actual ASS import/pipeline/export seam, controlled synthetic observations."""
    import json
    from pathlib import Path
    from unittest.mock import patch
    from lyricsync.export import export_project
    from lyricsync.model import valid_time
    from lyricsync.pipeline import run
    from lyricsync.subtitles import import_translation

    examples = Path(__file__).resolve().parents[1]/'examples'
    original = examples/'synthetic_ktv.ass'
    before = original.read_bytes()
    # Direct timed subtitle editing keeps both events and independent styles.
    editing_rows = import_translation(original)
    assert len(editing_rows) == 7
    assert [row.subtitle_metadata['style_name'] for row in editing_rows[:2]] == ['Default', 'Glow']
    truth = json.loads((examples/'truth.json').read_text(encoding='utf-8'))['cues']
    observations = [Visual(f'truth-{i}', cue['text'], cue['start'], cue['end'], [], .99, 5)
                    for i, cue in enumerate(truth)]
    p = Project(inputs={'video': str(examples/'synthetic_mv.mp4'), 'ktv': str(original),
                        'translation': str(examples/'synthetic_bilingual.txt')})
    with patch('lyricsync.pipeline.scan', return_value=(observations, {'duration': 15.5,
                                                                     'width': 960, 'height': 540})):
        run(p, tmp_path/'source-recovery.json')
    assert len(p.lyrics) == len(p.mappings) == 6
    assert len(p.lyrics[0].event_ids) == 2
    assert p.lyrics[0].id != p.lyrics[-1].id
    assert all(valid_time(row.start, row.end) for row in p.mappings)
    assert [row.visual_ids for row in p.mappings] == [[visual.id] for visual in observations]
    for row in p.mappings:
        row.confirmed = row.locked = True
    p.settings['parser_acknowledged'] = True
    export_project(p, tmp_path/'reviewed.ass')
    assert (tmp_path/'reviewed.ass').is_file()
    assert p.visuals == observations
    assert original.read_bytes() == before
