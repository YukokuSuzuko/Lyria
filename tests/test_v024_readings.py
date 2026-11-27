"""No model inference or GUI: real methods compiled with small UI doubles."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from lyricsync.japanese_readings import temporary_reading_input, pure_symbol_row, prepare_reading_rows
from lyricsync.bundled_reading import BUNDLED_RUNTIME

def method(name, **names):
    tree=ast.parse((Path(__file__).parents[1]/'desktop_qt/local_alignment_dialog.py').read_text(encoding='utf-8'))
    node=next(n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef) and n.name==name)
    ns=dict(deepcopy=deepcopy,**names)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<dialog-method>','exec'),ns)
    return ns[name]

class ReadingRegression(unittest.TestCase):
    def test_symbols(self):
        for value in ('・・・','ー','(´・∀・｀)','(^o^)','(つд⊂)','(艸^_^艸)'):
            self.assertTrue(pure_symbol_row({'text':value}),value)

    def test_idempotence_and_original_owners(self):
        for value in ('((^o^))桜〜','（(ω^)）歌〜','明日(ashita)〜','Party〜','Ｇｏ〜','つд⊂','Hello (world)'):
            clean,owners=temporary_reading_input(value)
            self.assertEqual(temporary_reading_input(clean)[0],clean,value)
            self.assertEqual(len(clean),len(owners))
            self.assertEqual(owners,sorted(set(owners)))
        self.assertEqual(temporary_reading_input('Ｇｏ〜')[0],'Go')
        self.assertEqual(temporary_reading_input('Party〜')[0],'Party')
        self.assertEqual(temporary_reading_input('歌〜')[0],'歌ー')

    def test_no_ordinary_lyrics_deleted(self):
        for text in ('Hello (world)','歌(つづく)','(^love^)','艸'):
            self.assertFalse(pure_symbol_row({'text':text}))
        self.assertEqual(temporary_reading_input('Hello (world)')[0],'Hello (world)')

    def test_symbols_have_persistable_empty_reading(self):
        row=prepare_reading_rows([{'id':'s','text':'・・・'}],tool='provided')[0]
        self.assertEqual(row['reading'],'')
        self.assertEqual(row['alignment_skip'],'nonvocal_symbols')

    def test_manual_reading_unchanged(self):
        source={'id':'a','text':'桜','reading':'custom','reading_origin':'manual'}
        self.assertEqual(prepare_reading_rows([source],tool='provided')[0]['reading'],'custom')
        self.assertEqual(source['reading'],'custom')

    def test_bundled_generation_bypasses_external_path_validator(self):
        calls=[];rows=[{'id':'a','text':'桜','reading':'manual'}]
        d=NS(_task=False,_reading_ready={'sudachi':True},reading_tool=NS(currentData=lambda:'sudachi'),
             _reading_python=lambda:BUNDLED_RUNTIME,_source_rows=lambda:rows,_sync_readings=lambda r:None,
             _current_readings=lambda:deepcopy(rows),document=NS(id='doc'),
             status=NS(setText=lambda message:self.fail(message)),
             _start_task=lambda work,action:calls.append(work(lambda:False,lambda *args:None)))
        with patch('lyricsync.local_alignment_runtime.validate_runtime_path',side_effect=AssertionError('external validator called')), patch('lyricsync.local_alignment_runtime.run_worker',return_value={'rows':rows}) as worker:
            method('_generate_readings')(d,reset=True)
        self.assertEqual(worker.call_args.args[1],BUNDLED_RUNTIME)
        self.assertTrue(worker.call_args.args[0]['reset_readings'])
        self.assertTrue(calls[0]['reset_readings'])
        self.assertEqual(calls[0]['requested_rows'],rows)

    def test_reset_unready_preserves_manual_rows(self):
        rows=[{'id':'a','text':'桜','reading':'handwritten'}];messages=[]
        d=NS(_task=False,_reading_ready={},reading_tool=NS(currentData=lambda:'sudachi'),
             _reading_rows=rows,status=NS(setText=messages.append))
        method('_reset_readings')(d)
        self.assertEqual(rows[0]['reading'],'handwritten')
        self.assertIn('保留',messages[0])

    def test_reset_ready_only_dispatches_without_clearing(self):
        rows=[{'id':'a','text':'桜','reading':'handwritten'}];calls=[]
        d=NS(_task=False,_reading_ready={'sudachi':True},reading_tool=NS(currentData=lambda:'sudachi'),
             _reading_rows=rows,_reading_python=lambda:BUNDLED_RUNTIME,
             _question=lambda *a:1,_generate_readings=lambda **kw:calls.append(kw))
        method('_reset_readings',QMessageBox=NS(StandardButton=NS(Yes=1)))(d)
        self.assertEqual(rows[0]['reading'],'handwritten')
        self.assertEqual(calls,[{'reset':True}])

    def test_probe_uses_effective_runtime_snapshot(self):
        messages=[];tips=[]
        d=NS(_service=NS(action='reading_probe',context={'runtime_python':'other.exe','reading_runtime_python':'','effective_reading_python':BUNDLED_RUNTIME}),
             _reading_python=lambda:BUNDLED_RUNTIME,status=NS(setText=messages.append),
             manage_reading_tool=NS(currentData=lambda:'sudachi'),_reading_ready={},
             reading_tool=NS(findData=lambda tool:-1,setCurrentIndex=lambda i:None),
             reading_tool_status=NS(setText=messages.append,setToolTip=tips.append),
             reading_badge=NS(setText=lambda s:None),_busy_state=lambda:None,_maybe_generate_readings=lambda:None)
        method('_outcome',QTimer=NS(singleShot=lambda *args:None))(d,{'reading_tool':{'tool':'sudachi','ready':True,'version':'0.6.11'}},None)
        self.assertTrue(d._reading_ready['sudachi'])
        self.assertFalse(any('旧探测' in s for s in messages))
        self.assertIn('工具版本：0.6.11',tips[0])
        d._service.context['effective_reading_python']='stale.exe'
        method('_outcome')(d,{},None)
        self.assertIn('旧探测',messages[-1])

if __name__=='__main__':unittest.main()
