"""0.2 foundation contracts: real model/editor/document paths, no API calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from lyricsync.model import Project, Mapping, Translation
from lyricsync.timebase import Timebase, validate_boundaries
from lyricsync.editing import Editor, History, split_mapping, merge_mappings
from lyricsync.frames import FrameIndex
from lyricsync.project_document import ProjectDocument, write_document, load_document, FORMAT_VERSION


def sample():
    return Project(translations=[Translation('t1','first'),Translation('t2','second')],
        mappings=[Mapping('s1',['t1'],start=0,end=1),Mapping('s2',['t2'],start=1,end=2)])


class FoundationTests(unittest.TestCase):
    def test_single_main_timeline(self):
        p=sample();d=ProjectDocument(p)
        self.assertIs(p.main_timeline,p.mappings)
        self.assertIs(d.main_timeline,p.mappings)
        self.assertEqual(p.mappings[0].sentence_id,'s1')

    def test_identity_survives_text_time_reorder_save_reopen(self):
        p=sample();h=History();e=Editor(p,h)
        e.set_text('s1','edited');e.set_time('s1',start=.1,end=.9)
        p.mappings.reverse();d=ProjectDocument(p)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'foundation.lyria'
            write_document(d.snapshot(explicit_save=True),path)
            reopened=load_document(path)
        self.assertEqual([r.id for r in reopened.main_timeline],['s2','s1'])
        self.assertEqual(reopened.main_timeline[1].text_override,'edited')
        self.assertEqual(reopened.project.timebase,Timebase().to_dict())
        self.assertEqual(FORMAT_VERSION,4)

    def test_split_merge_ancestry_roundtrip(self):
        p=sample();a,b=split_mapping(p,'s1',2,.5)
        self.assertNotEqual(a.id,b.id)
        self.assertNotIn('s1',[a.id,b.id])
        self.assertEqual(a.lineage_ids,['s1']);self.assertEqual(b.lineage_ids,['s1'])
        merged=merge_mappings(p,[a.id,b.id])
        self.assertEqual(set(merged.lineage_ids),{'s1',a.id,b.id})
        restored=Project.from_dict(p.to_dict())
        self.assertEqual(restored.mappings[0].lineage_ids,merged.lineage_ids)

    def test_razor_undo_redo_preserves_created_ids(self):
        p=sample();h=History();e=Editor(p,h);index=FrameIndex([0,.25,.5,.75,1,1.5],2)
        a,b=e.razor_split('s1',3,index);ids=[a.id,b.id]
        self.assertEqual(a.lineage_ids,['s1']);self.assertEqual(b.lineage_ids,['s1'])
        undone,_=h.restore(p);self.assertEqual(undone.mappings[0].id,'s1')
        redone,_=h.restore(undone,True)
        self.assertEqual([r.id for r in redone.mappings[:2]],ids)

    def test_batch_is_one_command_and_one_notification(self):
        p=sample();h=History(context=lambda:'s1');changes=[];e=Editor(p,h,lambda:changes.append(1))
        original=deepcopy(p.to_dict())
        self.assertTrue(e.set_times({'s1':{'end':.9},'s2':{'start':1.1}}))
        self.assertEqual(len(changes),1);self.assertEqual(len(h.undo_stack),1)
        restored,_=h.restore(p);self.assertEqual(restored.to_dict(),original)
        self.assertEqual(h.restored_context,'s1')
        redone,_=h.restore(restored,True)
        self.assertEqual((redone.mappings[0].end,redone.mappings[1].start),(.9,1.1))

    def test_batch_failure_is_atomic(self):
        for updates in ({'s1':{'end':.9},'s2':{'end':.5}},
                        {'s1':{'end':.9},'missing':{'start':1}},
                        {'s1':{'end':.9},'s2':{'end':float('nan')}}):
            p=sample();h=History();e=Editor(p,h);before=p.to_dict()
            with self.assertRaises(ValueError):e.set_times(updates)
            self.assertEqual(p.to_dict(),before);self.assertEqual(h.undo_stack,[])

    def test_batch_respects_locks(self):
        p=sample();p.settings['manual_locked_ids']=['s2'];h=History();e=Editor(p,h)
        before=p.to_dict()
        with self.assertRaises(ValueError):e.set_times({'s1':{'end':.9},'s2':{'start':1.1}})
        self.assertEqual(p.to_dict(),before);self.assertEqual(h.undo_stack,[])

    def test_noop_keeps_redo(self):
        p=sample();h=History();e=Editor(p,h);e.set_time('s1',end=.9)
        p,_=h.restore(p);e.project=p
        redo=deepcopy(h.redo_stack)
        self.assertFalse(e.set_times({'s1':{'end':1}}))
        self.assertEqual(h.redo_stack,redo);self.assertEqual(h.undo_stack,[])

    def test_transaction_exception_and_invalid_draft_preserve_live(self):
        p=sample();h=History();before=p.to_dict()
        with self.assertRaises(RuntimeError):
            with h.transaction(p,'abort') as draft:
                draft.mappings.clear();raise RuntimeError('abort')
        self.assertEqual(p.to_dict(),before);self.assertEqual(h.undo_stack,[])
        with self.assertRaises(ValueError):
            with h.transaction(p,'invalid') as draft:draft.mappings[1].id='s1'
        self.assertEqual(p.to_dict(),before);self.assertEqual(h.undo_stack,[])

    def test_nested_transaction_is_rejected_without_partial_commit(self):
        p=sample();h=History();before=p.to_dict()
        with self.assertRaises(RuntimeError):
            with h.transaction(p,'outer') as draft:
                draft.mappings.clear()
                with h.transaction(p,'inner'):pass
        self.assertEqual(p.to_dict(),before)
        with h.transaction(p,'valid') as draft:draft.mappings[0].text_override='ok'
        self.assertEqual(len(h.undo_stack),1)

    def test_invalid_history_restore_does_not_consume_either_stack(self):
        p=sample();h=History();e=Editor(p,h);e.set_time('s1',end=.9)
        h.undo_stack[-1][1]['version']=999
        before=deepcopy((h.undo_stack,h.redo_stack))
        with self.assertRaises(ValueError):h.restore(p)
        self.assertEqual((h.undo_stack,h.redo_stack),before)
        self.assertEqual(p.mappings[0].end,.9)

    def test_vfr_frame_boundaries_use_pts_not_average_fps(self):
        stamps=[0,.033,.09,.101,.2];index=FrameIndex(stamps,.3)
        self.assertEqual(index.interval(2,4),(.033,.2))
        p=sample();p.mappings[0].start=.033;p.mappings[0].end=.101
        e=Editor(p,History());e.move_frames('s1',1,index)
        self.assertEqual((p.mappings[0].start,p.mappings[0].end),(.09,.2))
        stamps[1]=9
        self.assertEqual(index.stamp(2),.033)

    def test_time_validation_rejects_nonfinite_boolean_negative_and_reverse(self):
        for start,end in [(True,2),(-1,2),(0,float('nan')),(0,float('inf')),(1,1),(2,1)]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):validate_boundaries(start,end)
        for stamps,duration in [([0,True],2),([-.1,0],1),([0,1],float('nan')),([0,1],float('inf'))]:
            with self.subTest(stamps=stamps,duration=duration),self.assertRaises(ValueError):FrameIndex(stamps,duration)
        self.assertEqual(validate_boundaries(None,1),(None,1))
        self.assertEqual(validate_boundaries(None,None),(None,None))

    def test_document_rejects_unknown_clock_and_legacy_envelope(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'bad.lyria'
            for mutate in (lambda s:s.update(format_version=1),
                           lambda s:s['project']['timebase'].update(unit='frames'),
                           lambda s:s['project'].pop('timebase')):
                snapshot=ProjectDocument(sample()).snapshot();mutate(snapshot)
                with self.assertRaises(ValueError):write_document(snapshot,path)
                self.assertFalse(path.exists())

    def test_document_replace_preserves_owner_and_saved_revision(self):
        d=ProjectDocument(sample());d.mark_saved(0);identity=d.id
        p=sample();p.mappings[0].text_override='replacement'
        self.assertTrue(d.replace_project(p));self.assertEqual(d.id,identity)
        self.assertEqual((d.revision,d.saved_revision),(1,0))
        p.mappings[0].text_override='external mutation'
        self.assertEqual(d.project.mappings[0].text_override,'replacement')
        self.assertFalse(d.replace_project(d.project));self.assertEqual(d.revision,1)
        invalid=sample()
        with self.assertRaises(ValueError):invalid.mappings[1].id='s1'
        self.assertEqual(invalid.mappings[1].id,'s2')
        malformed=invalid.to_dict();malformed.pop('alignment_graph')
        malformed['mappings'][1]['id']='s1'
        with self.assertRaises(ValueError):Project.from_dict(malformed)
        self.assertEqual(d.project.mappings[0].text_override,'replacement')

    def test_existing_ai_apply_keeps_lineage_and_reversible_ids(self):
        from tests.test_api_migration_alignment import fixture,split,merge,response
        from lyricsync.api_alignment import make_plan,accept_plan
        p,index=fixture();p.mappings[0].lineage_ids=['earlier']
        plan=make_plan(p,index,response(split(),merge()));h=History()
        accept_plan(p,plan,index,h)
        self.assertEqual(p.mappings[0].lineage_ids,['earlier','m0'])
        self.assertEqual(p.mappings[1].lineage_ids,['earlier','m0'])
        self.assertEqual(p.mappings[2].lineage_ids,['m1','m2'])
        ids=[row.id for row in p.mappings]
        undone,_=h.restore(p);redone,_=h.restore(undone,True)
        self.assertEqual([row.id for row in redone.mappings],ids)
        self.assertEqual(redone.mappings[0].lineage_ids,['earlier','m0'])

    def test_document_copy_does_not_alias_lineage(self):
        p=sample();p.mappings[0].lineage_ids=['ancestor'];d=ProjectDocument(p)
        snap=d.snapshot();snap['project']['mappings'][0]['lineage_ids'].append('other')
        self.assertEqual(p.mappings[0].lineage_ids,['ancestor'])


if __name__=='__main__':unittest.main()
