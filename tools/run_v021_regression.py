"""v0.21 complete isolated Windows regression runner; not foreground or model acceptance."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def code_manifest():
    paths=[]
    for folder in ('lyricsync','desktop_qt','tests','tools'):
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.qml') and '__pycache__' not in p.parts)
    paths.extend(ROOT/name for name in ('app_qt.py','LyriaQt.spec') if (ROOT/name).is_file())
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2, choices=(1, 2, 3, 4))
    parser.add_argument('modules', nargs='*')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen=code_manifest()
    (out/'source-before.json').write_text(json.dumps(frozen,indent=2),encoding='utf-8')
    modules = args.modules or sorted(p.name for p in (ROOT/'tests').glob('test_*.py'))
    (out/'inventory.json').write_text(json.dumps({'modules':modules,'count':len(modules),'source':'explicit arguments' if args.modules else 'all test_*.py'},indent=2),encoding='utf-8')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QPA_FONTDIR=r'C:\Windows\Fonts', PYTHONDONTWRITEBYTECODE='1',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               LYRICSYNC_TESSERACT=str(ROOT/'resources/tesseract/tesseract.exe'))
    def run(module):
        folder = out/module.removesuffix('.py')
        folder.mkdir()
        junit = folder/'report.xml'
        start = time.monotonic()
        with (folder/'output.log').open('w', encoding='utf-8') as log:
            try:
                done = subprocess.run([sys.executable, '-B', str(ROOT/'tools/pytest_process.py'), '-q',
                                       str(ROOT/'tests'/module), '-p', 'no:cacheprovider', '--junitxml='+str(junit),
                                       '--basetemp='+str(folder/'tmp')], cwd=ROOT, env=env,
                                      stdout=log, stderr=subprocess.STDOUT, timeout=240,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                code = done.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        row = dict(module=module, exit=code, seconds=round(time.monotonic()-start, 2))
        if junit.exists():
            suite = next(ET.parse(junit).getroot().iter('testsuite'))
            row.update({k: int(suite.attrib[k]) for k in ('tests', 'failures', 'errors', 'skipped')})
        print(json.dumps(row), flush=True)
        return row
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, module) for module in modules]
        for future in as_completed(futures):
            results.append(future.result())
            (out/'summary.json').write_text(json.dumps(sorted(results, key=lambda r:r['module']), indent=2), encoding='utf-8')
    after=code_manifest()
    changed=sorted(key for key in set(frozen)|set(after) if frozen.get(key)!=after.get(key))
    (out/'source-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
    (out/'source-stability.json').write_text(json.dumps({'stable':not changed,'changed':changed},indent=2),encoding='utf-8')
    return int(bool(changed) or any(r['exit'] != 0 or r.get('failures', 0) or r.get('errors', 0) for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
