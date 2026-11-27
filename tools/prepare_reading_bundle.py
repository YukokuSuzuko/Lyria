"""Collect only verified Sudachi packages, never a Python environment or GPL reader."""
import argparse,base64,csv,hashlib,json,shutil
from pathlib import Path
PACKAGES={'sudachipy':'0.6.11','sudachidict_small':'20260116'}
def prepare(site,output,license_dir):
    site=Path(site).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Output must be new; never overwrite a bundle')
    selected={}
    for package,version in PACKAGES.items():
        info=site/(package+'-'+version+'.dist-info')
        metadata=(info/'METADATA').read_text('utf-8')
        if '\nVersion: '+version+'\n' not in metadata:raise ValueError('Version mismatch')
        record={r[0]:r[1:] for r in csv.reader((info/'RECORD').open(encoding='utf-8'))}
        for p in (site/package).rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts:continue
            selected[p.relative_to(site).as_posix()]=p
        for name in ('METADATA','WHEEL','top_level.txt','LICENSE-2.0.txt'):
            p=info/name
            if p.is_file():selected[p.relative_to(site).as_posix()]=p
        for relative,p in list(selected.items()):
            if not relative.startswith((package+'/',info.name+'/')):continue
            if p.is_symlink() or not p.resolve().is_relative_to(site):raise ValueError('Unsafe source path')
            entry=record.get(relative)
            if not entry or not entry[0].startswith('sha256='):raise ValueError('Missing wheel RECORD digest: '+relative)
            digest=base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).rstrip(b'=').decode()
            if entry[0]!='sha256='+digest or int(entry[1])!=p.stat().st_size:raise ValueError('Wheel RECORD mismatch: '+relative)
    for name in ('SudachiPy-LICENSE.txt','SudachiDict-LICENSE-2.0.txt','SudachiDict-LEGAL.txt','SOURCES.json'):
        selected['licenses/'+name]=Path(license_dir)/name
    output.mkdir(parents=True)
    files={}
    for name,p in sorted(selected.items()):
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
        with target.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        files[name]={'bytes':target.stat().st_size,'sha256':digest}
    manifest={'schema':1,'versions':PACKAGES,'files':files}
    path=output/'manifest.json';path.write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
    return {'files':len(files),'bytes':sum(v['bytes'] for v in files.values()),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--site-packages',required=True);p.add_argument('--output',required=True);p.add_argument('--licenses',required=True);a=p.parse_args()
    print(json.dumps(prepare(a.site_packages,a.output,a.licenses)))
