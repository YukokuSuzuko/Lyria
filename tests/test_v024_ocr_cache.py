import copy
import unittest
from lyricsync.qc_ocr_cache import CachedOCR

class CacheSchemaTests(unittest.TestCase):
    def sample(self):
        leaf = dict(text="test", box=[0, 2, 40, 2], confidence=.7, source_channels=["raw"])
        component = dict(leaf, spatial_split="observed_horizontal_edge_bands", provenance=[dict(leaf)])
        return [dict(text="test", box=leaf["box"], confidence=.7,
                     evidence=dict(line_slot=[2, 2], components=[component]))]

    def test_extended_component_and_degenerate_slot(self):
        self.assertTrue(CachedOCR.valid(self.sample()))

    def test_untrusted_fields_rejected(self):
        value=self.sample()
        value[0]["evidence"]["components"][0]["arbitrary"]=True
        self.assertFalse(CachedOCR.valid(value))

    def test_reversed_slot_and_recursive_provenance_rejected(self):
        value=self.sample()
        value[0]["evidence"]["line_slot"]=[3,2]
        self.assertFalse(CachedOCR.valid(value))
        value=self.sample()
        value[0]["evidence"]["components"][0]["provenance"][0]["provenance"]=[]
        self.assertFalse(CachedOCR.valid(value))


    def test_extended_components_write_and_persistent_hit(self):
        import tempfile
        import numpy as np
        from pathlib import Path
        from lyricsync.storage import Cache
        from lyricsync.model import Detection
        class Provider:
            signature={"name":"schema-fixture"}
            calls=0
            def recognize(provider,image):
                provider.calls+=1
                return [Detection(**copy.deepcopy(self.sample()[0]))]
        provider=Provider()
        with tempfile.TemporaryDirectory(prefix="v024-ocr-cache-") as folder:
            cache=Cache(Path(folder)/"cache.sqlite")
            try:
                wrapped=CachedOCR(provider,cache,"fixture",[0,0,40,10],{})
                image=np.zeros((10,40,3),np.uint8)
                first=wrapped.recognize(image)
                wrapped.memory.clear()
                second=wrapped.recognize(image)
                self.assertEqual(first,second)
                self.assertEqual(provider.calls,1)
                self.assertEqual(wrapped.metrics["persistent_hits"],1)
            finally:
                cache.db.close()

if __name__ == "__main__":
    unittest.main()