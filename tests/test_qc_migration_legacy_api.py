"""Removed image-provider UI cannot break the retained text-settings entry.

No Tk root, credential store, OS dialogs or network are instantiated: compile
only the method under test and supply inert controls/callback recorders.
"""
import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]


def method(path,cls,name,namespace):
    tree=ast.parse(path.read_text(encoding='utf8'))
    owner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    node=next(n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    return namespace[name]


class Var:
    def __init__(self,value=''):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class Control:
    def __init__(self,*args,**kwargs):self.destroyed=False
    def pack(self,*args,**kwargs):return self
    def destroy(self):self.destroyed=True


class LegacyImageRemovalTests(unittest.TestCase):
    def test_text_settings_open_and_save_without_any_image_fields(self):
        buttons={};keys=[];saved=[];audit_saved=[];autosaved=[]
        window=Control()
        class FakeKey(Control):
            def __init__(self,parent,scope,label):
                super().__init__();self.entry=Var();keys.append((scope,label))
            def save(self):raise AssertionError('An empty key must not trigger credential writes')
        def button(parent,label,callback,*args):
            buttons[label]=callback
            return Control()
        def unexpected_error(*args,**kwargs):raise AssertionError(args)
        namespace={'tk':SimpleNamespace(StringVar=Var),
                   'ttk':SimpleNamespace(Checkbutton=Control,Frame=Control,Label=Control,Entry=Control),
                   'KeyWidget':FakeKey,'TEXT_SCOPE':'text:deepseek','button':button,
                   'ai_config':SimpleNamespace(save=lambda home,settings:saved.append(dict(settings))),
                   'messagebox':SimpleNamespace(showerror=unexpected_error,showwarning=unexpected_error)}
        entry=method(ROOT/'lyricsync/gui.py','Application','api_settings',namespace)
        with tempfile.TemporaryDirectory() as directory:
            app=SimpleNamespace(guard=lambda:True,popup=lambda *a:window,ai_enabled=Var(True),
                ai_toggle=lambda:None,project=SimpleNamespace(settings={'api_model':'retained-model','api_max_calls':50,'api_retries':3}),
                home=Path(directory),audit_panel=SimpleNamespace(save_settings=lambda:audit_saved.append(True)),
                autosave=lambda:autosaved.append(True))
            entry(app)
            self.assertEqual(keys,[('text:deepseek','DeepSeek 文字 API')])
            self.assertIn('测试文字 AI 连接',buttons)
            self.assertIn('保存设置',buttons)
            self.assertFalse(any('图片' in label for label in buttons))
            buttons['保存设置']()
            self.assertEqual(saved,[{'api_enabled':True,'api_model':'retained-model','api_max_calls':50,'api_retries':3}])
            self.assertEqual(audit_saved,[True]);self.assertEqual(autosaved,[True])
            self.assertTrue(window.destroyed)

    def test_text_error_redaction_is_preserved_without_image_key_attribute(self):
        logs=[];dialogs=[]
        namespace={'time':SimpleNamespace(strftime=lambda pattern:'test-time '),
                   'messagebox':SimpleNamespace(showerror=lambda *args:dialogs.append(args))}
        error=method(ROOT/'lyricsync/gui.py','Application','error',namespace)
        with tempfile.TemporaryDirectory() as directory:
            app=SimpleNamespace(api_key='FAKE_MAIN_KEY',audit_text_key='FAKE_QC_KEY',
                                status=Var(),write_log=logs.append,home=Path(directory))
            error(app,RuntimeError('FAKE_MAIN_KEY / FAKE_QC_KEY'))
            stored=(app.home/'logs/errors.log').read_text()
            for value in (app.status.get(),str(logs),str(dialogs),stored):
                self.assertNotIn('FAKE_MAIN_KEY',value);self.assertNotIn('FAKE_QC_KEY',value)
                self.assertIn('[REDACTED]',value)

    def test_generic_key_scope_error_no_longer_references_removed_feature(self):
        identity=method(ROOT/'lyricsync/key_widget.py','KeyWidget','identity',{})
        self.assertEqual(identity(SimpleNamespace(scope='text:custom')),'text:custom')
        self.assertEqual(identity(SimpleNamespace(scope=lambda:'text:deepseek')),'text:deepseek')
        with self.assertRaises(ValueError) as caught:identity(SimpleNamespace(scope=''))
        self.assertNotIn('图片',str(caught.exception))
        self.assertIn('API 服务标识',str(caught.exception))

    def test_no_production_image_api_configuration_or_request_symbols_remain(self):
        forbidden={'image_audit','image_api','image_enabled','image_endpoint','image_model',
                   'image_scope','image_key','audit_image_key','send_images'}
        hits=[]
        for path in (ROOT/'lyricsync').rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf8'))
            for node in ast.walk(tree):
                symbol=(node.attr if isinstance(node,ast.Attribute) else
                        node.id if isinstance(node,ast.Name) else
                        node.name if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) else None)
                if symbol in forbidden:hits.append(f'{path.name}:{node.lineno}:{symbol}')
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value=='image_url':
                    hits.append(f'{path.name}:{node.lineno}:image_url request field')
        self.assertEqual(hits,[])


if __name__=='__main__':unittest.main()
