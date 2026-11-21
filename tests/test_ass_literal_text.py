"""Literal text must survive ASS rendering and Lyria's marked round trip."""
from pathlib import Path
import tempfile
import unittest
from lyricsync.export import escape_text, render_ass, export_project
from lyricsync.model import Project, Mapping, Translation
from lyricsync.subtitles import ass_plain, parse_ass, recover_lyrics


class AssLiteralTextTests(unittest.TestCase):
    def test_backslashes_are_not_doubled_or_converted_to_linebreaks(self):
        self.assertEqual(escape_text(r'A\NB\nC\hD\T'), 'A\\\u2060NB\\\u2060nC\\\u2060hD\\\u2060T')
        self.assertEqual(escape_text('A\r\nB\rC\nD'), r'A\NB\NC\ND')

    def test_override_looking_text_remains_literal_and_has_safe_brace_block(self):
        text = r'A{\alpha&HFF&}{\p1}B}'
        encoded = escape_text(text)
        self.assertIn(r'\{{}', encoded)
        plain, tags, warnings = ass_plain(encoded, lyria_escapes=True)
        self.assertEqual(plain, text)
        self.assertEqual(tags, [])
        self.assertEqual(warnings, [])

    def test_marked_round_trip_preserves_original_joiners_and_unicode(self):
        texts = [r'春\N風\n雨\h雪', 'A' + '\\' * 3 + 'B', r'{\pos(0,0)}逐字保留',
                 '連\\\u2060接\\\u2060\u2060符', '👨‍👩‍👧‍👦e\u0301\\N',
                 '第一行\n第二行', '{}{}{{}}末尾\\']
        with tempfile.TemporaryDirectory() as folder:
            for n,text in enumerate(texts):
                with self.subTest(text=text):
                    target=Path(folder)/f'{n}.ass'
                    target.write_text(render_ass([(0,2,text)],font_size=20),encoding='utf8')
                    doc=parse_ass(target)
                    self.assertEqual(doc.events[0].text,text)
                    self.assertEqual('\n'.join(row.text for row in recover_lyrics(doc)),text)

    def test_unmarked_external_joiner_is_not_removed(self):
        self.assertEqual(ass_plain('A\\\u2060NB')[0], 'A\\\u2060NB')
        # Existing legacy imported escapes retain their previous behavior.
        self.assertEqual(ass_plain(r'A\\B\{C\}')[0], 'A\\B{C}')

    def test_export_does_not_mutate_project_or_change_srt_lrc_text(self):
        text=r'逐字\N{\alpha&HFF&}'
        p=Project(video={'width':1920,'height':1080,'duration':2})
        p.translations=[Translation('t',text)]
        p.mappings=[Mapping('m',['t'],start=0,end=1,confirmed=True)]
        snapshot=p.to_dict()
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('ass','srt','lrc'):
                path=Path(folder)/('literal.'+suffix)
                export_project(p,path)
                self.assertEqual(p.to_dict(),snapshot)
                if suffix=='ass':self.assertEqual(parse_ass(path).events[0].text,text)
                else:self.assertIn(text,path.read_text(encoding='utf-8-sig'))


    def test_partial_export_never_overwrites_unrelated_omission_report(self):
        p=Project(video={'width':1920,'height':1080,'duration':2})
        p.translations=[Translation('a','有时间'),Translation('b','未定时')]
        p.mappings=[Mapping('a',['a'],start=0,end=1),Mapping('b',['b'])]
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'partial.ass'
            report=Path(folder)/'partial_遗漏清单.json'
            report.write_bytes(b'original report bytes')
            with self.assertRaisesRegex(FileExistsError,'遗漏清单'):
                export_project(p,output,skip_missing_times=True)
            self.assertEqual(report.read_bytes(),b'original report bytes')
            self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
