from dataclasses import asdict
from tests.ai_pairing_helpers import pairing_reply
import tempfile
import unittest
from pathlib import Path
from lyricsync.model import Project, Mapping, Translation
from lyricsync.editing import split_mapping, merge_mappings, mapping_text
from lyricsync.frames import FrameIndex
from lyricsync.storage import save_project, load_project
from lyricsync.export import render_ass
from lyricsync.subtitles import parse_ass


class RevisionTests(unittest.TestCase):
    def fixture(self):
        return Project(translations=[Translation('a','终有一天'),Translation('b','我们一定能够'),Translation('c','心意相通')],
            mappings=[Mapping('a',['a'],start=1,end=2),Mapping('b',['b']),Mapping('c',['c'],start=3,end=4)])

    def test_missing_time_and_repeated_merge(self):
        p=self.fixture()
        a=merge_mappings(p,{'a','b'})
        self.assertEqual(mapping_text(p,a),'终有一天我们一定能够')
        self.assertEqual((a.start,a.end),(1,2))
        a=merge_mappings(p,{a.id,'c'})
        self.assertEqual(mapping_text(p,a),'终有一天我们一定能够心意相通')
        self.assertFalse(a.confirmed)

    def test_missing_time_split_with_or_without_marker(self):
        p=self.fixture()
        a,b=split_mapping(p,'b',2,None)
        self.assertEqual((a.start,a.end,b.start,b.end),(None,None,None,None))
        self.assertEqual(mapping_text(p,a)+mapping_text(p,b),'我们一定能够')
        p=self.fixture();a,b=split_mapping(p,'b',2,2.5)
        self.assertEqual((a.start,a.end,b.start,b.end),(None,2.5,2.5,None))

    def test_manual_edits_recovery_and_markers(self):
        from lyricsync.editing import Editor, History
        p=self.fixture();editor=Editor(p,History())
        editor.set_text('a','新的中文')
        editor.set_time('a',start=1.2,end=2.2)
        self.assertEqual(mapping_text(p,p.mappings[0]),'新的中文')
        self.assertEqual(p.translations[0].text,'终有一天')
        editor.add_marker(35);editor.split_at_marker('a',2,35,FrameIndex([i/20 for i in range(100)],5))
        self.assertEqual(p.settings['frame_markers'],[35])
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'project.json';save_project(p,target);q=load_project(target)
            self.assertEqual(q.to_dict(),p.to_dict())
            save_project(q,target);target.write_text('{broken',encoding='utf8')
            q=load_project(target,recover=True)
            self.assertEqual(mapping_text(q,q.mappings[0]),'新的')

    def test_ass_roundtrip_preserves_visible_frames(self):
        # Reproduction: blind centisecond rounding delays starts at n/30 by a frame.
        index=FrameIndex([i/30 for i in range(120)],4)
        entries=[(*index.interval(i,i+2),str(i)) for i in range(2,110,4)]
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'test.ass'
            target.write_text(render_ass(entries,frame_index=index),encoding='utf8')
            events=parse_ass(target).events
        for entry,event in zip(entries,events):
            self.assertEqual((index.first(event.start),index.last(event.end)),
                             (index.first(entry[0]),index.last(entry[1])))

    def test_blank_gap_and_confirm_are_separate(self):
        from lyricsync.editing import Editor, History
        p=self.fixture();editor=Editor(p,History())
        editor.fill_gap('b');self.assertEqual((p.mappings[1].start,p.mappings[1].end),(2,3))
        self.assertFalse(p.mappings[1].confirmed)
        editor.confirm('b');editor.confirm('b',False)
        self.assertEqual((p.mappings[1].start,p.mappings[1].end),(2,3))
        blank=editor.insert_blank('b');editor.set_text(blank.id,'补充')
        self.assertEqual(mapping_text(p,blank),'补充')

    def test_ruby_is_separated_only_in_original_karaoke(self):
        from lyricsync.subtitles import ass_plain
        raw=r'{\k10}僕|<ぼ{\k10}#|く{\k15}らもう一|<い{\k10}#|ち{\k20}度|<ど{\k10}その先|<さ{\k10}#|き{\k20}へ'
        self.assertEqual(ass_plain(raw,True)[0],'僕らもう一度その先へ')
        self.assertIn('|',ass_plain(raw)[0])
        self.assertEqual(ass_plain('a|b # <test>',True)[0],'a|b # <test>')

    def test_dual_input_and_rescan_preserve_edits_order_and_missing_time(self):
        from unittest.mock import patch
        from lyricsync.pipeline import run
        from lyricsync.model import Visual
        from lyricsync.editing import Editor,History
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);video=d/'stub.mp4';video.write_bytes(b'fixture')
            text=d/'zh.txt';text.write_text('你好\n再见',encoding='utf8')
            p=Project(inputs={'video':str(video),'translation':str(text)})
            evidence=[Visual('v1','HELLO',1,2,[0,0,10,10],.9,3),Visual('v2','GOODBYE',3,4,[0,0,10,10],.9,3)]
            with patch('lyricsync.pipeline.scan',return_value=(evidence,{'duration':5})):
                run(p,d/'p.json');self.assertEqual(len(p.mappings),2)
                ids=[m.id for m in p.mappings];edit=Editor(p,History())
                edit.set_text(ids[1],'人工修改');edit.set_time(ids[1],start=3.2)
                blank=edit.insert_blank(ids[0]);edit.set_text(blank.id,'补充歌词')
                run(p,d/'p.json')
            self.assertEqual([m.id for m in p.mappings],[ids[0],blank.id,ids[1]])
            self.assertEqual(mapping_text(p,p.mappings[-1]),'人工修改')
            self.assertEqual(p.mappings[-1].start,3.2)
            # The new insert command fills an available adjacent gap by default.
            # A later OCR rerun must preserve that human-owned gap allocation.
            self.assertEqual((p.mappings[1].start,p.mappings[1].end),(2,3.2))

    def test_rescan_keeps_original_until_explicit_adoption_and_is_reversible(self):
        from lyricsync.rescan import rescan_cue,adopt_rescan
        from lyricsync.editing import Editor,History
        from lyricsync.model import Visual
        p=self.fixture();p.inputs={'video':'stub.mp4'};p.video={'duration':5}
        before=asdict(p.mappings[1]);history=History()
        def scanner(path,settings,cache,refs,**kw):
            self.assertEqual(settings['scan_range'],[2,3])
            self.assertTrue(settings['rescan_token'])
            return [Visual('v','NEW',2.1,2.9,[0,0,10,10],.8,4)],{}
        rescan_cue(p,'b',None,(2,3),scanner=scanner)
        self.assertEqual(asdict(p.mappings[1]),before)
        adopt_rescan(Editor(p,history),'b',['v'])
        self.assertEqual((p.mappings[1].start,p.mappings[1].end),(2.1,2.9))
        p,_=history.restore(p);self.assertEqual(asdict(p.mappings[1]),before)

    def test_ass_quantization_at_common_rates_and_variable_pts(self):
        import bisect
        for rate in (24,25,30,50,60,24000/1001,30000/1001,60000/1001):
            index=FrameIndex([i/rate for i in range(600)],600/rate)
            for stamp in index.stamps:
                fixed=index.ass_boundary(stamp)
                self.assertEqual(index.first(stamp),index.first(fixed))
                self.assertEqual(index.last(stamp),index.last(fixed))
                ms=[int(t*1000+.5) for t in index.stamps]
                aegisub_start=min(len(ms),bisect.bisect_right(ms,round(fixed*1000)-1)+1)
                self.assertEqual(index.first(stamp),aegisub_start)
        index=FrameIndex([0,.037,.071,.120,.161,.199,.244],.29)
        for stamp in index.stamps:self.assertEqual(index.first(stamp),index.first(index.ass_boundary(stamp)))
        # Establish that the old nearest-centisecond algorithm really crosses frames.
        index=FrameIndex([i/30 for i in range(300)],10)
        self.assertGreater(sum(index.first(t)!=index.first(round(t*100)/100) for t in index.stamps),0)

    def test_api_full_coverage_cache_and_auth_failure_do_not_edit_results(self):
        from lyricsync.api import suggest
        from lyricsync.storage import Cache
        from urllib.error import HTTPError
        p=self.fixture();p.settings.update(api_enabled=True,api_max_calls=2,api_retries=0)
        before=[asdict(m) for m in p.mappings]
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(Path(d)/'cache.sqlite');calls=[]
            def transport(body,key):
                calls.append(body);return pairing_reply(body),{}
            try:
                suggest(p,cache,'fake',transport=transport)
                suggest(p,cache,'fake',transport=transport)
                self.assertEqual(len(calls),1);self.assertEqual(p.settings['api_coverage']['processed'],3)
                self.assertEqual([asdict(m) for m in p.mappings],before)
                p.settings['api_model']='different-model'
                def fail(*a):raise HTTPError('https://example.invalid',401,'SECRET',{},None)
                result=suggest(p,cache,'fake',transport=fail)
                self.assertIn('authentication_failed',result[0]['error'])
                self.assertNotIn('SECRET',str(result))
            finally:cache.close()

    def test_ai_config_is_shared_without_persisting_secrets(self):
        from lyricsync import ai_config
        with tempfile.TemporaryDirectory() as d:
            ai_config.save(d,dict(api_model='test-model',api_max_calls=3,api_retries=0,api_key='SECRET'))
            result=ai_config.load(d)
            self.assertEqual(result['api_model'],'test-model')
            self.assertNotIn('SECRET',(Path(d)/'ai_settings.json').read_text(encoding='utf-8'))

    def test_relocating_identical_files_preserves_edits_and_rejects_different_content(self):
        from lyricsync.storage import relocate_inputs,file_hash
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);same=d/'same.txt';same.write_text('原文',encoding='utf8')
            other=d/'other.txt';other.write_text('不同',encoding='utf8')
            p=self.fixture();p.inputs={'ktv':str(d/'missing.txt')}
            p.settings['input_hashes']={'ktv':file_hash(same)}
            before=asdict(p.mappings[0])
            relocate_inputs(p,{'ktv':str(same)})
            with self.assertRaises(ValueError):relocate_inputs(p,{'ktv':str(other)})
            self.assertEqual(p.inputs['ktv'],str(same));self.assertEqual(asdict(p.mappings[0]),before)

    def test_requested_unavailable_gpu_is_not_silently_called_accelerated(self):
        from unittest.mock import patch
        from lyricsync.vision import RapidOCRProvider
        with patch('onnxruntime.get_available_providers',return_value=['CPUExecutionProvider']):
            with self.assertRaisesRegex(RuntimeError,'不支持 cuda'):RapidOCRProvider(device='cuda')

if __name__=='__main__': unittest.main()
