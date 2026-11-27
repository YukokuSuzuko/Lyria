"""Rematching preserves sentence identity without equating repeated text."""
from unittest.mock import patch
import unittest
from lyricsync.model import Project,Mapping,Translation,Lyric
from lyricsync.matching import match_project


def sample():
    return Project(translations=[Translation('t1','repeat'),Translation('t2','repeat')],
        mappings=[Mapping('import-1',['t1'],lineage_ids=['ancestor-1']),
                  Mapping('import-2',['t2'],lineage_ids=['ancestor-2'])])


class RematchIdentityTests(unittest.TestCase):
    def test_unique_occurrences_keep_identity_and_ancestry_repeatedly(self):
        p=sample()
        for _ in range(3):
            match_project(p)
            self.assertEqual([(r.id,r.lineage_ids) for r in p.mappings],
                [('import-1',['ancestor-1']),('import-2',['ancestor-2'])])

    def test_actual_merge_then_split_gets_fresh_ids_and_ancestors(self):
        p=sample()
        merged=[([],p.translations,.5,[])]
        with patch('lyricsync.matching._ordered_groups',return_value=merged):match_project(p)
        row=p.mappings[0];mid=row.id
        self.assertNotIn(mid,('import-1','import-2'))
        self.assertEqual(set(row.lineage_ids),{'ancestor-1','import-1','ancestor-2','import-2'})
        with patch('lyricsync.matching._ordered_groups',return_value=merged):match_project(p)
        self.assertEqual(p.mappings[0].id,mid)
        match_project(p)
        self.assertEqual(len(p.mappings),2)
        self.assertTrue(all(r.id not in ('import-1','import-2',mid) and mid in r.lineage_ids for r in p.mappings))
        self.assertNotEqual(p.mappings[0].id,p.mappings[1].id)

    def test_ambiguous_old_group_does_not_pick_arbitrary_identity(self):
        p=sample();p.mappings.append(Mapping('ambiguous',['t1'],lineage_ids=['other']))
        match_project(p)
        row=next(r for r in p.mappings if r.translation_ids==['t1'])
        self.assertNotIn(row.id,('import-1','ambiguous'))
        self.assertEqual(set(row.lineage_ids),{'ancestor-1','import-1','other','ambiguous'})
        self.assertEqual(next(r.id for r in p.mappings if r.translation_ids==['t2']),'import-2')

    def test_complementary_merge_keeps_target_id_without_self_ancestry(self):
        from lyricsync.editing import merge_mappings
        p=Project(translations=[Translation('t','text')],mappings=[
            Mapping('text-only',['t'],lineage_ids=['shared']),
            Mapping('timed',[],start=1,end=2,text_override='',lineage_ids=['shared'])])
        merged=merge_mappings(p,['text-only','timed'])
        self.assertEqual(merged.id,'timed')
        self.assertEqual(merged.lineage_ids,['shared','text-only'])
        p.validate_timeline()
        merged.lineage_ids.append('timed')
        with self.assertRaises(ValueError):p.validate_timeline()

    def test_locked_sentence_is_untouched(self):
        p=sample();p.mappings[0].locked=True;original=p.mappings[0]
        match_project(p)
        self.assertIs(p.mappings[0],original)
        self.assertEqual(p.mappings[1].id,'import-2')

    def test_reordered_source_rows_do_not_exchange_repeated_sentence_ids(self):
        p=sample();p.translations.reverse();match_project(p)
        self.assertEqual({tuple(r.translation_ids):r.id for r in p.mappings},
            {('t1',):'import-1',('t2',):'import-2'})

if __name__=='__main__':unittest.main()
