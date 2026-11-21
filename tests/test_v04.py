from pathlib import Path
from copy import deepcopy
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
from lyricsync.model import Project,Mapping,Translation,Visual,Lyric
from lyricsync.editing import History,split_mapping,merge_mappings,mapping_text
from lyricsync.export import export_project,validate_project,layout_entries,render_ass
from lyricsync.frames import FrameIndex
from lyricsync.vision import Video
from lyricsync.storage import Cache,save_project,load_project
from lyricsync.api import translate_reference
from lyricsync.credentials import KeyStore


def fixture():
    return Project(translations=[Translation('t','你好，世界。')],
        lyrics=[Lyric('l','HELLO WORLD',1,4)],
        visuals=[Visual('v','HELLO WORLD',1,4,[10,900,100,950],.9,5)],
        mappings=[Mapping('m',['t'],['l'],['v'],1,4,.95,[],True,True)],
        video={'width':1920,'height':1080,'duration':10})


class EditingTests(unittest.TestCase):
    def test_rounded_touching_boundary_does_not_allocate_overlap_lane(self):
        edge=67.00026666666666
        rows=layout_entries([(61.8618,67.000267,'前一句'),(edge,71.10436666666666,'后一句')],position='top')
        self.assertEqual(rows[0]['y'],rows[1]['y'])
        real_overlap=layout_entries([(61.8618,edge+.033,'前一句'),(edge,71.10436666666666,'后一句')],position='top')
        self.assertEqual(real_overlap[0]['y'],real_overlap[1]['y']);self.assertTrue(real_overlap[1]['overlapping'])
    def test_split_preserves_characters_outer_times_and_undo(self):
        p=fixture();before=p.to_dict();h=History();h.push(p,'split')
        a,b=split_mapping(p,'m',3,2.5)
        self.assertEqual(mapping_text(p,a)+mapping_text(p,b),'你好，世界。')
        self.assertEqual((a.start,a.end,b.start,b.end),(1,2.5,2.5,4))
        self.assertEqual(p.translations[0].text,'你好，世界。')
        self.assertEqual(validate_project(p,draft=True),[])
        self.assertFalse(a.confirmed or b.confirmed)
        p,label=h.restore(p);self.assertEqual(p.to_dict(),before)
        p,label=h.restore(p,True);self.assertEqual(len(p.mappings),2)
    def test_merge_reconstructs_split_exactly(self):
        p=fixture();a,b=split_mapping(p,'m',3,2.5)
        m=merge_mappings(p,{a.id,b.id})
        self.assertEqual(mapping_text(p,m),'你好，世界。');self.assertEqual((m.start,m.end),(1,4))
    def test_missing_fragment_blocks_draft(self):
        p=fixture();a,b=split_mapping(p,'m',3,2.5);p.mappings.remove(b)
        self.assertTrue(any('未分配' in x for x in validate_project(p,draft=True)))
    def test_draft_skips_review_but_not_missing_times(self):
        p=fixture();p.mappings[0].confirmed=False;p.mappings[0].confidence=.4
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'draft.ass';export_project(p,path,draft=True)
            self.assertIn('UNREVIEWED DRAFT',path.read_text(encoding='utf-8-sig'))
            self.assertFalse(p.mappings[0].confirmed)
            p.mappings[0].start=None
            with self.assertRaises(ValueError):export_project(p,Path(folder)/'invalid.ass',draft=True)
    def test_split_persists_and_rescan_retains_fragments(self):
        from lyricsync.matching import match_project
        p=fixture();split_mapping(p,'m',3,2.5)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'p.json';save_project(p,path);q=load_project(path)
        match_project(q)
        self.assertEqual([mapping_text(q,m) for m in q.mappings],['你好，','世界。'])
    def test_invalid_split_does_not_change_project(self):
        p=fixture();before=p.to_dict()
        for character,cut in ((0,2),(3,0),(3,5)):
            with self.assertRaises(ValueError):split_mapping(p,'m',character,cut)
        self.assertEqual(p.to_dict(),before)
    def test_layout_used_by_ass_keeps_overlapping_lines_at_user_anchor(self):
        entries=[(1,4,'甲'),(2,5,'乙'),(3,6,'丙')]
        for position in ('top','bottom','custom','above_original'):
            rows=layout_entries(entries,position=position,source_top=900)
            self.assertEqual(len({r['y'] for r in rows}),1)
            body=render_ass(entries,position=position,source_top=900)
            for row in rows:self.assertIn(f"\\pos({row['x']},{row['y']})",body)
            if position=='above_original':self.assertTrue(all(r['y']<900 for r in rows))
    def test_vfr_frame_numbers_use_pts_inclusive_last(self):
        idx=FrameIndex([0,.04,.12,.15],.2)
        self.assertEqual(idx.interval(2,3),(.04,.15))
        self.assertEqual(idx.last(.15),3)
        self.assertEqual(idx.interval(4,4),(.15,.2))
        with self.assertRaises(ValueError):idx.interval(0,1)
    def test_actual_frame_index_matches_decoded_video(self):
        video=Video(Path(__file__).resolve().parents[1]/'examples/synthetic_mv.mp4')
        idx=FrameIndex.build(video)
        self.assertEqual(len(idx.stamps),310)
        self.assertAlmostEqual(idx.interval(21,22)[0],1)
        self.assertAlmostEqual(idx.interval(21,22)[1],1.1)


class ReferenceAPITests(unittest.TestCase):
    def test_text_only_reference_cache_and_no_project_mutation(self):
        p=fixture();before=deepcopy(p.to_dict());calls=[]
        with tempfile.TemporaryDirectory() as folder:
            cache=Cache(Path(folder)/'a.db')
            def transport(body,key):
                calls.append(body)
                self.assertTrue(all(isinstance(m['content'],str) for m in body['messages']))
                payload=json.loads(body['messages'][1]['content'])
                self.assertEqual(payload,{'line_id':'l','text':'HELLO WORLD'})
                return '{"line_id":"l","translation":"你好世界","note":""}',{}
            for _ in range(2):translate_reference('HELLO WORLD','l',cache,'fake',transport=transport)
            self.assertEqual(len(calls),1);cache.close()
        self.assertEqual(p.to_dict(),before)
    def test_reference_invalid_json_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Cache(Path(folder)/'a.db')
            with self.assertRaises(RuntimeError):translate_reference('HELLO','l',cache,'fake',retries=0,transport=lambda *a:('bad',{}))
            self.assertEqual(cache.records()[0]['status'],'invalid_response');cache.close()


class NativeCredentialsTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('LYRICSYNC_TEST_CREDENTIALS')=='1','explicit native credential test')
    def test_saved_blank_preserved_reopen_and_delete(self):
        store=KeyStore('MV_LyricSync/test/'+uuid.uuid4().hex+'/');scope='dummy-test-only'
        try:
            self.assertTrue(store.save(scope,'dummy-local-test'))
            self.assertFalse(store.save(scope,''))
            self.assertEqual(KeyStore(store.namespace).get(scope),'dummy-local-test')
            store.delete(scope);self.assertEqual(store.get(scope),'')
        finally:store.delete(scope)


if __name__=='__main__':unittest.main()
