"""Diagnostic pure Tcl/Tk root lifecycle, private desktop required."""
import gc,json,os,sys,traceback
import tkinter as tk
from tools.private_desktop import desktop_name
assert desktop_name()==os.environ['LYRIA_EXPECTED_TEST_DESKTOP']
mode=sys.argv[1];failures=[]
for i in range(40):
    root=None
    try:
        root=tk.Tk();root.withdraw();root.update_idletasks()
        library=root.tk.eval('set tk_library')
        root.probe_cycle=root
        root.destroy()
    except Exception:
        failures.append(i);traceback.print_exc()
    finally:
        root=None
        if mode=='gc':gc.collect()
    print(json.dumps(dict(iteration=i,mode=mode,failed=i in failures)),flush=True)
print(json.dumps(dict(total=40,mode=mode,failures=failures)),flush=True)
