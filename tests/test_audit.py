from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from lyricsync.audit import local_audit,text_audit,validate_audit_response,chat_request


class AuditTests(unittest.TestCase):
    def report(self):
        return {"version":1,"inputs":{},"cues":[{"id":"c1","text":"你好","start":1,"end":2,
            "expected_texts":["你好"],"reference_lyrics":["HELLO"],"evidence":"test"}],"issues":[],"warnings":[],"scope":"test",
            "api":{"text":{"status":"not_run","checked_ids":[]},"image":{"status":"disabled","checked_ids":[]}}}
    def test_text_api_calls_then_uses_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            report=self.report();calls=[]
            def transport(endpoint,key,body):
                calls.append(body)
                self.assertNotIn("SECRET",str(body));self.assertNotIn("video",body["messages"][1]["content"])
                return '{"checked_ids":["c1"],"issues":[]}',{"total_tokens":42}
            text_audit(report,folder,"SECRET",maximum=2,transport=transport)
            self.assertEqual(report["api"]["text"]["status"],"completed")
            text_audit(report,folder,"SECRET",maximum=2,transport=transport)
            self.assertEqual(len(calls),1)
    def test_invalid_api_checked_coverage_is_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            report=self.report()
            text_audit(report,folder,"secret",transport=lambda *a:('{"checked_ids":[],"issues":[]}',{}))
            self.assertEqual(report["api"]["text"]["status"],"partial")
    def test_text_api_excludes_media_even_after_images_are_prepared(self):
        with tempfile.TemporaryDirectory() as folder:
            report=self.report()
            report["inputs"]={"video":"PRIVATE_VIDEO.mp4"}
            report["cues"][0].update(screenshot="PRIVATE_SCREENSHOT.png",
                                    image_url="data:image/png;base64,PRIVATE_IMAGE",
                                    frames=["PRIVATE_FRAME.png"])
            calls=[]
            def transport(endpoint,key,body):
                calls.append(body)
                self.assertTrue(all(isinstance(m["content"],str) for m in body["messages"]))
                self.assertNotIn("PRIVATE_",json.dumps(body))
                self.assertNotIn("image_url",json.dumps(body))
                payload=json.loads(body["messages"][1]["content"])
                self.assertEqual(set(payload["cues"][0]),
                                 {"id","text","expected_texts","reference_lyrics","evidence","start","end"})
                self.assertIn("no screenshots or video",body["messages"][0]["content"])
                return '{"checked_ids":["c1"],"issues":[]}',{}
            text_audit(report,folder,"secret",transport=transport)
            self.assertEqual(len(calls),1)
    def test_image_api_has_been_removed(self):
        import lyricsync.audit as audit
        self.assertFalse(hasattr(audit,'image_audit'))

    def test_endpoint_rejects_http_and_embedded_key(self):
        for url in ("http://example.com/chat/completions","https://secret@example.com/chat/completions"):
            with self.assertRaises(ValueError):chat_request(url,"key",{})
    def test_repeated_translation_occurrence_is_not_reported_missing(self):
        root=Path(__file__).resolve().parents[1]
        # The fixture harness explicitly confirms known synthetic truth. The
        # production pipeline must not autoapprove uncertain mappings.
        output=root.parent/"reports"/"synthetic"/"synthetic_reviewed_中文.ass"
        if not output.is_file():self.skipTest("run tests/run_pipeline.py first")
        with tempfile.TemporaryDirectory() as folder:
            report=local_audit(root/"examples"/"synthetic_mv.mp4",root/"examples"/"synthetic_zh.txt",folder,ass_path=output)
            self.assertFalse([i for i in report["issues"] if i["kind"]=="missing"])


if __name__=="__main__":unittest.main()
