from pathlib import Path
import os
import sys
def initialize():
    sys.dont_write_bytecode=True
    install=Path(sys.executable).resolve().parent.parent
    override=os.environ.get('LYRIA_STATE_DIR','').strip()
    state=Path(override) if override else install/'state'
    for key,name in {'TEMP':'tmp','TMP':'tmp','TMPDIR':'tmp','APPDATA':'roaming','LOCALAPPDATA':'local','NUMBA_CACHE_DIR':'numba','HF_HOME':'hf','TORCH_HOME':'torch','XDG_CACHE_HOME':'cache','MPLCONFIGDIR':'mpl','CUDA_CACHE_PATH':'cuda'}.items():
        old=os.environ.get(key,'')
        if old and Path(old).is_absolute() and Path(old).drive.lower()==install.drive.lower():continue
        folder=state/name;folder.mkdir(parents=True,exist_ok=True);os.environ[key]=str(folder)
    os.environ.update(QML_DISABLE_DISK_CACHE='1',CUDA_CACHE_DISABLE='1',HF_HUB_DISABLE_TELEMETRY='1')

