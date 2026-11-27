"""Diagnostic only: full node coverage, retained failures, no application edits."""
import ast,json,os,subprocess,sys,time
from pathlib import Path
from tools.private_desktop import desktop_name
root=Path(__file__).resolve().parents[1]
assert desktop_name()==os.environ['LYRIA_EXPECTED_TEST_DESKTOP']
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
tree=ast.parse((root/'tests/test_gui_playback.py').read_text('utf-8'))
nodes=sorted(n.name for cls in tree.body if isinstance(cls,ast.ClassDef) and cls.name=='PlaybackTests' for n in cls.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_'))
results=[]
cases=([(f'whole-{i+1}','tests/test_gui_playback.py') for i in range(5)] if '--repeat-whole' in sys.argv else [('whole-before','tests/test_gui_playback.py')]+[(n,'tests/test_gui_playback.py::PlaybackTests::'+n) for n in nodes]+[('whole-after','tests/test_gui_playback.py')])
for name,node in cases:
    directory=out/name;directory.mkdir()
    with (directory/'output.log').open('w',encoding='utf-8') as log:
        start=time.monotonic()
        try:
            done=subprocess.run([sys.executable,'-B',str(root/'tools/pytest_process.py'),*(['-p','tools.tk_gc_probe'] if '--gc-probe' in sys.argv else []),*(['-p','tools.tk_thread_probe'] if '--thread-probe' in sys.argv else []),*(['-p','tools.tk_shared_root_probe'] if '--shared-root-probe' in sys.argv else []),'-q',node,'--junitxml='+str(directory/'report.xml'),'--basetemp='+str(directory/'tmp')],cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW,timeout=90)
            code=done.returncode
        except subprocess.TimeoutExpired:code='timeout'
    row=dict(node=node,name=name,returncode=code,seconds=round(time.monotonic()-start,2));results.append(row)
    print(json.dumps(row),flush=True);(out/'results.json').write_text(json.dumps(results,indent=2),'utf-8')
print('NODES_COVERED',len(nodes),flush=True)
