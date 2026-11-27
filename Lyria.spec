# Build from this directory with Python 3.12 x64 and requirements.txt.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

root=Path(SPECPATH)
datas=[(str(root/'resources'),'resources')]
datas+=collect_data_files('rapidocr',excludes=['models/*'])
hidden=collect_submodules('rapidocr')+['onnxruntime','pytesseract','av','PIL.ImageTk','tkinter']
binaries=collect_dynamic_libs('onnxruntime')
a=Analysis([str(root/'app.py')],pathex=[str(root)],binaries=binaries,datas=datas,
           hiddenimports=hidden,hookspath=[],hooksconfig={},runtime_hooks=[],
           excludes=['paddle','torch','openvino','pytest','matplotlib','IPython','notebook'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Lyria',debug=False,
        bootloader_ignore_signals=False,strip=False,upx=False,console=False,
        disable_windowed_traceback=False,argv_emulation=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Lyria')
