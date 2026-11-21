from tests.ai_pairing_helpers import pairing_reply
from pathlib import Path
import json
import tempfile
import unittest

from lyricsync.model import *
from lyricsync.subtitles import *
from lyricsync.export import *
from lyricsync.storage import *
from lyricsync.matching import match_project
from lyricsync.api import validate_response,suggest
from lyricsync.audit import validate_audit_response,text_audit


HEADER="""[Script Info]
ScriptType: v4.00+
PlayResX: 960
PlayResY: 540
[V4+ Styles]
Format: Name, Fontname, Fontsize, Alignment
Style: Default,Arial,30,2
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def file(self,name,body):
        p=self.folder/name;p.write_text(body,encoding="utf-8");return p
    def ass(self,events):return self.file("in.ass",HEADER+events)
    def test_ass_karaoke_commas_and_escapes(self):
        p=self.ass(r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\k20}Hi,{\K30} world\N{\kf10}夜{\ko10}空{\kt4}\h好"+"\n")
        d=parse_ass(p)
        self.assertEqual(d.events[0].text,"Hi, world\n夜空\u00a0好")
        self.assertEqual(len(recover_lyrics(d)),2)
        self.assertEqual(d.events[0].start,1)
    def test_drawing_state_and_transforms(self):
        text,tags,warnings=ass_plain(r"{\p1}m 0 0 l 20 20{\p0}ABC{\t(0,100,\p1)}DEF")
        self.assertEqual(text,"ABCDEF")
    def test_reordered_fields_and_case(self):
        p=self.file("order.ass","[Events]\nFormat: Style, End, Layer, Start, Text\nDialogue: A,0:00:03.00,1,0:00:01.00,hello,world\n")
        event=parse_ass(p).events[0]
        self.assertEqual((event.text,event.layer,event.start),("hello,world","1",1))
    def test_effect_duplicates_and_true_three_way_overlap(self):
        p=self.ass("\n".join([f"Dialogue: {layer},0:00:0{a}.00,0:00:0{b}.00,Default,,0,0,0,,{text}"
            for layer,a,b,text in ((0,1,5,"HELLO"),(1,1,5,r"{\bord4}HELLO"),(0,2,6,"WORLD"),(0,3,7,"NIGHT"),(0,8,9,"HELLO"))]))
        lyrics=recover_lyrics(parse_ass(p))
        self.assertEqual(len(lyrics),4)
        self.assertEqual(lyrics[0].end,5)
        self.assertEqual(len(lyrics[0].event_ids),2)
        self.assertNotEqual(lyrics[0].id,lyrics[3].id)
    def test_same_text_at_distinct_positions_is_retained(self):
        p=self.ass(r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\pos(100,100)}HI"+"\n"+
            r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\pos(100,200)}HI")
        self.assertEqual(len(recover_lyrics(parse_ass(p))),2)
    def test_malformed_events_not_silent(self):
        p=self.ass("Dialogue: 0,0:00:03.00,0:00:01.00,Default,,0,0,0,,bad\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,good")
        self.assertTrue(parse_ass(p).warnings)
    def test_positioned_character_reconstruction(self):
        p=self.ass("\n".join(f"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{{\\pos({100+i*30},200)}}{c}" for i,c in enumerate("ABC")))
        units=recover_lyrics(parse_ass(p))
        self.assertEqual(len(units),1);self.assertEqual(units[0].text,"ABC");self.assertTrue(units[0].warnings)
    def test_translation_txt_preserves_and_groups(self):
        p=self.file("zh.txt","  你好！ \n世界\n\n不改写\n")
        self.assertEqual(import_translation(p)[0].text,"  你好！ ")
        self.assertEqual(import_translation(p,True)[0].text,"  你好！ \n世界")
    def test_bilingual_tsv_is_explicit(self):
        p=self.file("zh.txt","HELLO\t你好\nHELLO\t你好\n")
        items=import_translation(p)
        self.assertEqual(items[0].reference,"HELLO")
        self.assertNotEqual(items[0].id,items[1].id)
    def test_utf16_translation(self):
        p=self.folder/"utf16.txt";p.write_bytes("你好\n世界".encode("utf-16"))
        self.assertEqual(import_translation(p)[0].text,"你好")
    def test_srt_and_ass_translation(self):
        p=self.file("zh.srt","1\n00:00:01,000 --> 00:00:03,000\n你好\n世界\n")
        self.assertEqual(import_translation(p)[0].text,"你好\n世界")
        p=self.ass(r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\k2}你好\N世界")
        self.assertEqual(import_translation(p)[0].text,"你好\n世界")
    def project(self):
        return Project(video={"width":960,"height":540,"duration":20},lyrics=[Lyric("l","HELLO",1,3)],
            translations=[Translation("t","你好")],visuals=[Visual("v","HELLO",2,4,[100,300,400,340],.95,4)],
            mappings=[Mapping("m",["t"],["l"],["v"],2,4,.95)])
    def test_export_chinese_only_preserves_anchor_for_overlaps(self):
        p=self.project();p.translations+=[Translation("t2","第二句\n仍然存在"),Translation("t3","第三句")]
        p.mappings+=[Mapping("m2",["t2"],["l"],["v"],3,5,.95),Mapping("m3",["t3"],["l"],["v"],3.5,6,.95)]
        for m in p.mappings:m.confirmed=True
        output=self.folder/"out.ass";export_project(p,output)
        body=output.read_text(encoding="utf-8-sig")
        self.assertNotIn("HELLO",body);self.assertNotRegex(body,r"\\[kK](?:f|o|t)?\d")
        events=parse_ass(output).events
        self.assertEqual([e.end for e in events],[4,5,6])
        self.assertEqual(len({tuple(e.position) for e in events}),1)
    def test_export_rejects_unresolved_missing_and_invalid_time(self):
        p=self.project();p.mappings[0].reasons=["不确定"]
        with self.assertRaises(ValueError):export_project(p,self.folder/"a.ass")
        p.mappings[0].confirmed=True;p.mappings[0].end=float("nan")
        with self.assertRaises(ValueError):export_project(p,self.folder/"a.ass")
    def test_no_overwrite_original(self):
        source=self.ass("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,HELLO")
        before=source.read_bytes()
        with self.assertRaises(ValueError):convert_ktv(source,source)
        self.assertEqual(before,source.read_bytes())
    def test_converter_keeps_overlap_default(self):
        p=self.ass("Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,{\\k1}HELLO\nDialogue: 0,0:00:02.00,0:00:05.00,Default,,0,0,0,,WORLD")
        output=self.folder/"converted.ass";convert_ktv(p,output)
        self.assertEqual(parse_ass(output).events[0].end,4)
    def test_persistence_preserves_lock_and_filters_key(self):
        p=self.project();p.mappings[0].locked=True;p.settings["api_key"]="SECRET"
        path=self.folder/"p.json";save_project(p,path)
        self.assertNotIn("SECRET",path.read_text(encoding='utf-8'))
        q=load_project(path);match_project(q)
        self.assertTrue(q.mappings[0].locked);self.assertEqual(q.mappings[0].start,2)
    def test_counts_differ_never_silent(self):
        p=self.project();p.lyrics+=[Lyric("l2","WORLD",3,5)]
        match_project(p)
        self.assertTrue(p.unresolved())
        self.assertTrue(p.mappings[0].reasons)
    def test_visual_absence_never_uses_ktv_times(self):
        p=self.project();p.visuals=[];match_project(p)
        self.assertIsNone(p.mappings[0].start)
    def test_reference_mapping_supports_many_to_one(self):
        p=self.project();p.lyrics+=[Lyric("l2","WORLD",3,5)]
        p.translations[0].reference="HELLO WORLD"
        p.visuals+=[Visual("v2","WORLD",4,6,[100,350,400,390],.97,5)]
        match_project(p)
        self.assertEqual(len(p.mappings[0].lyric_ids),2)
        self.assertEqual((p.mappings[0].start,p.mappings[0].end),(2,6))
    def test_cache_budget_persists_across_restart(self):
        path=self.folder/"cache.db";cache=Cache(path);cache.reserve_call("a",1);cache.close()
        cache=Cache(path)
        with self.assertRaises(RuntimeError):cache.reserve_call("b",1)
        cache.close()
    def test_api_invalid_json_unknown_ids_and_timing(self):
        for raw in ("not json",'{"mappings":[],"corrections":[],"time":3}',
            '{"mappings":[{"lyric_ids":["UNKNOWN"],"translation_ids":["t"],"confidence":0.9,"reason":"x"}],"corrections":[]}'):
            with self.assertRaises((ValueError,TypeError)):validate_response(raw,["l"],["t"])
    def test_api_disabled_does_not_call(self):
        p=self.project();cache=Cache(self.folder/"c.db")
        def reject(*args):self.fail("must not call API")
        self.assertEqual(suggest(p,cache,"",transport=reject),[]);cache.close()
    def test_api_failure_preserves_results_and_redacts_key(self):
        p=self.project();p.mappings[0].confidence=.4;p.settings.update(api_enabled=True,api_max_calls=1,api_retries=0)
        cache=Cache(self.folder/"c.db")
        def fail(*args):raise OSError("SECRET_KEY")
        result=suggest(p,cache,"SECRET_KEY",transport=fail)
        self.assertNotIn("SECRET_KEY",json.dumps(result));self.assertEqual(p.mappings[0].start,2);cache.close()
    def test_api_valid_suggestions_never_change_locked(self):
        p=self.project();p.mappings[0].locked=True;p.mappings[0].confidence=.1;p.settings["api_enabled"]=True
        cache=Cache(self.folder/"c.db")
        before=p.mappings[0].__dict__.copy()
        result=suggest(p,cache,"key",transport=lambda *a:(pairing_reply(a[0]),{}))
        self.assertEqual(result[0]['status'],'processed')
        self.assertEqual(p.mappings[0].__dict__,before);cache.close()
    def test_main_text_api_sends_only_text_records(self):
        p=self.project();p.mappings[0].confidence=.4
        p.settings.update(api_enabled=True,api_max_calls=1,api_retries=0)
        p.inputs["video"]="PRIVATE_VIDEO.mp4"
        p.visuals[0].evidence={"screenshot":"PRIVATE_SCREENSHOT.png"}
        calls=[]
        def transport(body,key):
            calls.append(body)
            self.assertTrue(all(isinstance(m["content"],str) for m in body["messages"]))
            self.assertNotIn("PRIVATE_",json.dumps(body))
            self.assertNotIn("image_url",json.dumps(body))
            payload=json.loads(body["messages"][1]["content"])
            self.assertEqual(set(payload),{"lyrics","current_pairings"})
            return pairing_reply(body),{}
        cache=Cache(self.folder/"text-only.db")
        try:suggest(p,cache,"secret",transport=transport)
        finally:cache.close()
        self.assertEqual(len(calls),1)
    def test_api_budget_exhaustion_keeps_completed_suggestions(self):
        from copy import deepcopy
        p=self.project();p.mappings[0].confidence=.4
        second=deepcopy(p.mappings[0]);second.id="second";second.reasons=["different context"]
        rows=[deepcopy(p.mappings[0])]+[deepcopy(second) for _ in range(6)]
        for i,m in enumerate(rows):m.id=str(i)
        p.replace_timeline(rows)
        p.settings.update(api_enabled=True,api_max_calls=1,api_retries=0)
        cache=Cache(self.folder/"partial.db")
        try:
            suggest(p,cache,"key",transport=lambda *a:(pairing_reply(a[0]),{}))
            self.assertEqual(len(p.settings["api_suggestions"]),7)
            self.assertEqual({k:p.settings['api_coverage'][k] for k in ('total','processed','failed','pending')},
                             dict(total=7,processed=6,failed=0,pending=1))
            self.assertEqual(p.settings['api_coverage']['status'],'budget_exhausted')
            self.assertEqual(p.settings["api_suggestions"][0]["mapping_id"],p.mappings[0].id)
        finally:cache.close()
    def test_audit_schema_and_failure_status(self):
        good={"checked_ids":["c"],"issues":[]}
        self.assertEqual(validate_audit_response(good,["c"]),good)
        with self.assertRaises(ValueError):validate_audit_response({"checked_ids":["bad"],"issues":[]},["c"])
        report={"inputs":{},"cues":[{"id":"c","text":"你好","start":1,"end":3}],"issues":[],"warnings":[],"scope":"test", "api":{"text":{"status":"not_run","checked_ids":[]},"image":{"status":"disabled","checked_ids":[]}}}
        with self.assertRaises(RuntimeError):text_audit(report,self.folder,"SECRET",maximum=1,retries=0,transport=lambda *a:(_ for _ in ()).throw(OSError("SECRET")))
        self.assertEqual(report["api"]["text"]["status"],"failed")
        self.assertNotIn("SECRET",(self.folder/"report.json").read_text(encoding='utf-8'))


if __name__=="__main__":unittest.main()
