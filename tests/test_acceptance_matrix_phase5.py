"""Keep historical acceptance audit separate from synthetic validator coverage.

Full local docs: both classes execute; source-only export: only historical class
skips explicitly. Synthetic subclass retains every negative assertion and does
not attest that private historical requirements or evidence were revalidated.
"""
import copy
import json
from pathlib import Path
import unittest
from tools.render_acceptance_matrix import validate, render

ROOT = Path(__file__).resolve().parents[1]

class AcceptanceMatrixGateTests(unittest.TestCase):
    def setUp(self):
        path=ROOT / 'docs/PHASE5_ACCEPTANCE_MATRIX_20261112.json'
        if not path.is_file():
            self.skipTest('私有历史证据未随源码分发；此项实际历史审计未执行，不计通过')
        self.data = json.loads(path.read_text(encoding='utf-8'))
    def test_actual_matrix_has_complete_feedback_coverage(self):
        self.assertEqual(len(validate(self.data)), len(self.data['rows']))
    def test_missing_feedback_cannot_be_hidden(self):
        self.data['rows'] = [x for x in self.data['rows'] if x['id'] != 'D4-17']
        with self.assertRaisesRegex(ValueError, 'coverage missing'): validate(self.data)
    def test_duplicate_id_rejected(self):
        self.data['rows'].append(copy.deepcopy(self.data['rows'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'): validate(self.data)
    def test_visual_code_or_offscreen_is_not_acceptance(self):
        r=next(x for x in self.data['rows'] if x['id']=='D4-17')
        r['status']='已验证'; r['test_evidence']=[{'kind':'offscreen','path':'example.png'}]; r['actual_windows_evidence']=[]
        with self.assertRaisesRegex(ValueError, 'actual Windows'): validate(self.data)
    def test_verified_requires_evidence(self):
        r=next(x for x in self.data['rows'] if x['id']=='R03')
        r['status']='已验证';r['test_evidence']=[]
        with self.assertRaisesRegex(ValueError, 'without evidence'): validate(self.data)
    def test_blocked_requires_exact_reason(self):
        r=next(x for x in self.data['rows'] if x['id']=='R03')
        r['status']='受阻';r['blockers']=[]
        with self.assertRaisesRegex(ValueError, 'without reason'): validate(self.data)
    def test_render_includes_every_detail_and_test_limit(self):
        text=render(self.data)
        for row in self.data['rows']: self.assertIn('### '+row['id']+' · ',text)
        self.assertIn('待验，不得以离屏代替',text)
        self.assertIn('真实在线API禁止调用',text)


class SyntheticAcceptanceMatrixGateTests(AcceptanceMatrixGateTests):
    """Same validators/negative assertions, synthetic rows only, not past acceptance."""
    def setUp(self):
        ids=[f'D{v}-{i:02d}' for v,count in [(3,24),(4,27)] for i in range(1,count+1)]+['R03']
        self.data={'created_utc':'synthetic','status':'合成验证器夹具，不是历史验收','rows':[
            {'id':key,'priority':'P0','current_requirement':'Synthetic requirement',
             'owner':'fixture','status':'未做','title':'Synthetic row','status_scope':'unit fixture',
             'requires_actual_windows_visual':key=='D4-17','test_evidence':[],
             'actual_windows_evidence':[],'blockers':[],'notes':['真实在线API禁止调用']}
            for key in ids]}
