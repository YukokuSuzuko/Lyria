import unittest
from lyricsync.model import Project, Lyric, Translation, Mapping
from lyricsync.capsule_pairing import state, sources, operate

def sample():
    p=Project()
    p.lyrics=[Lyric('l1','原詞一',1.,2.),Lyric('l2','原詞二',3.,4.)]
    p.translations=[Translation('t1','甲👩‍👩‍👧‍👦乙'),Translation('t2','第二句')]
    p.mappings=[Mapping('m1',[],['l1'],start=1.,end=2.,text_role='source_import',text_override='原詞一'),
                Mapping('m2',[],['l2'],start=3.,end=4.,text_role='source_import',text_override='原詞二')]
    p.settings['pending_translation_ids']=['t1','t2']
    return p

class CapsulePairingTests(unittest.TestCase):
    def test_later_import_joins_pool_without_restoring_split_original(self):
        p=operate(sample(),'split',ids=['t1'],offset=1)
        p.translations.append(Translation('later','later text'))
        st=state(p)
        self.assertEqual(sum(x['id']=='later' for x in st['pieces']),1)
        self.assertFalse(any(x['id']=='t1' for x in st['pieces']))
        self.assertEqual([x['text'] for x in st['pieces'] if x['id']=='later'],['later text'])

    def test_append_does_not_overwrite_external_edit_and_orphan_can_unpair(self):
        p=operate(sample(),'source',ids=['m1']);gid=state(p)['groups'][0]['id']
        p=operate(p,'bind',ids=['t1'],target=gid)
        p.mappings[0].text_override='MANUAL CORRECTION'
        with self.assertRaises(ValueError):operate(p,'bind',ids=['t2'],target=gid)
        self.assertEqual(p.mappings[0].text_override,'MANUAL CORRECTION')
        p.mappings=p.mappings[1:]
        restored=operate(p,'remove',ids=[gid])
        self.assertEqual(state(restored)['groups'],[])
        self.assertIn('t1',restored.settings['pending_translation_ids'])

    def test_unpair_does_not_overwrite_other_new_metadata(self):
        p=operate(sample(),'source',ids=['m1']);gid=state(p)['groups'][0]['id']
        p=operate(p,'bind',ids=['t1'],target=gid)
        p.mappings[0].reasons.append('new manual reason')
        with self.assertRaises(ValueError):operate(p,'remove',ids=[gid])
        with self.assertRaises(ValueError):operate(p,'bind',ids=['t2'],target=gid)
        self.assertIn('new manual reason',p.mappings[0].reasons)

    def test_corrupt_saved_state_fails_without_modifying_project(self):
        p=sample();p.settings['translation_capsules_v022']={'version':1,'pieces':[],'groups':[{'id':'g','cue_id':'m1','piece_ids':['missing']}]}
        before=p.to_dict()
        with self.assertRaises(ValueError):state(p)
        self.assertEqual(p.to_dict(),before)

    def test_initial_middle_empty_originals_intact(self):
        p=sample();self.assertEqual(state(p)['groups'],[])
        self.assertEqual(len(sources(p)),2)
        before=p.to_dict()
        d=operate(p,'source',ids=['m1'])
        self.assertEqual(p.to_dict(),before)
        g=state(d)['groups'][0]
        bound=operate(d,'bind',ids=['t1'],target=g['id'])
        self.assertEqual(bound.lyrics,p.lyrics)
        self.assertEqual([(r.start,r.end) for r in bound.mappings],[(1.,2.),(3.,4.)])
        self.assertEqual(bound.translations[0].text,p.translations[0].text)
        self.assertEqual(bound.mappings[0].text_override,'甲👩‍👩‍👧‍👦乙')
        self.assertEqual(bound.settings['pending_translation_ids'],['t2'])
        restored=operate(bound,'remove',ids=[g['id']])
        self.assertEqual(restored.mappings[0].text_role,'source_import')
        self.assertEqual(restored.mappings[0].text_override,'原詞一')
        self.assertEqual(restored.settings['pending_translation_ids'],['t1','t2'])

    def test_split_merge_exact_text_and_pending_fragments(self):
        p=sample();d=operate(p,'split',ids=['t1'],offset=1)
        ps=state(d)['pieces'];self.assertEqual(ps[0]['text']+ps[1]['text'],p.translations[0].text)
        merged=operate(d,'merge',ids=[ps[0]['id'],ps[1]['id']])
        self.assertEqual(state(merged)['pieces'][0]['text'],p.translations[0].text)
        d=operate(d,'source',ids=['m1'])
        d=operate(d,'bind',ids=[ps[0]['id']],target=state(d)['groups'][0]['id'])
        self.assertIn('t1',d.settings['pending_translation_ids'])
        with self.assertRaises(ValueError):operate(p,'split',ids=['t1'],offset=3)

    def test_lock_stale_and_repeated_lyric_identity(self):
        p=sample();p.lyrics[1].text=p.lyrics[0].text
        d=operate(p,'source',ids=['m1','m2'])
        self.assertEqual(len(state(d)['groups']),2)
        with self.assertRaises(ValueError):operate(d,'source',ids=['m1'])
        p.mappings[0].locked=True
        with self.assertRaises(ValueError):operate(p,'source',ids=['m1'])

    def test_serialization_and_modified_row_no_silent_overwrite(self):
        d=operate(sample(),'source',ids=['m1']);g=state(d)['groups'][0]
        d=operate(d,'bind',ids=['t1'],target=g['id'])
        loaded=Project.from_dict(d.to_dict())
        self.assertEqual(state(d),state(loaded))
        loaded.mappings[0].text_override='后来手改'
        with self.assertRaises(ValueError):operate(loaded,'remove',ids=[g['id']])

    def test_split_ends_and_nonadjacent_merge_rejected(self):
        p=sample()
        for off in [0,len(p.translations[0].text)]:
            with self.assertRaises(ValueError):operate(p,'split',ids=['t1'],offset=off)
        d=operate(p,'split',ids=['t1'],offset=1)
        ps=state(d)['pieces']
        with self.assertRaises(ValueError):operate(d,'merge',ids=[ps[0]['id'],ps[2]['id']])

if __name__=='__main__':unittest.main()
