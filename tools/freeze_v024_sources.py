"""Freeze historical tracked paths plus explicit reviewed additions. No publication."""
from pathlib import Path, PurePosixPath
import argparse
import importlib.util
import json
import os
import subprocess
import tempfile

EDITIONS=("source","source-ocr-modified")
NEW_ROOTS={"lyricsync","desktop_qt","tests","tools"}
FORBIDDEN={"evidence","evidence-local","advice","validation",".git",".env"}

def git(root,*args):
    return subprocess.check_output(["git","-C",str(root),*args],
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))

def relative_path(name):
    path=PurePosixPath(name)
    if (not isinstance(name,str) or not name or path.is_absolute() or
        any(x in ("",".","..") for x in name.split("/")) or
        "\\" in name or ":" in name or "\0" in name):
        raise ValueError("Unsafe tracked path")
    return path

def inventory(source, baseline):
    tracked=sorted(filter(None,git(baseline,"ls-files","-z").decode("utf-8").split("\0")))
    for name in tracked:
        relative_path(name)
        p=source/name
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(source):
            raise ValueError("Missing/unsafe baseline file: "+name)
    candidates=[]
    for prefix in (*sorted(NEW_ROOTS),"docs/v0.24"):
        directory=source/prefix
        if directory.exists():
            for p in directory.rglob("*"):
                if p.is_file() and p.suffix.lower() in {".py",".qml",".json",".md",".ps1",".vbs"} and "__pycache__" not in p.parts:
                    name=p.relative_to(source).as_posix()
                    if name not in tracked:candidates.append(name)
    return tracked, sorted(set(candidates))

def freeze(root,baseline,extras,execute=False):
    if any(os.environ.get(k) for k in ("GIT_DIR","GIT_WORK_TREE","GIT_COMMON_DIR","GIT_INDEX_FILE","GIT_OBJECT_DIRECTORY","GIT_ALTERNATE_OBJECT_DIRECTORIES")):
        raise ValueError("Git redirection environment must be unset before inventory or init")
    root=Path(root).resolve();baseline=Path(baseline).resolve()
    if root==baseline or root.is_relative_to(baseline) or baseline.is_relative_to(root):
        raise ValueError("Baseline and working tree overlap")
    result={}
    for edition in EDITIONS:
        source=root/edition;old=baseline/edition
        if (source/".git").exists() or (source/".git").is_symlink():
            raise ValueError("Existing Git metadata; never overwrite")
        tracked,candidates=inventory(source,old)
        approved=extras.get(edition,[])
        if not isinstance(approved,list) or any(not isinstance(n,str) for n in approved):
            raise ValueError("Extras must be explicit path lists")
        for name in approved:
            rel=relative_path(name)
            if (any(x in FORBIDDEN or "private" in x.casefold() for x in rel.parts)
                or not (rel.parts[0] in NEW_ROOTS or name.startswith("docs/v0.24/"))
                or name not in candidates):
                raise ValueError("Unapproved extra path: "+name)
        pending=sorted(set(candidates)-set(approved))
        result[edition]={"baseline_commit":git(old,"rev-parse","HEAD").decode().strip(),
                         "tracked_count":len(tracked),"extra_files":approved,"pending_review":pending}
        if execute and pending:raise ValueError("Review additions before freezing: "+repr(pending))
    if not execute:return result
    for edition in EDITIONS:
        source=root/edition
        tracked,_=inventory(source,baseline/edition)
        files=sorted(set(tracked+extras.get(edition,[])))
        git(source,"init","--template=","--initial-branch=v0.24")
        spec=importlib.util.spec_from_file_location("v024_git_safety",Path(__file__).with_name("git_workspace_safety.py"))
        safety=importlib.util.module_from_spec(spec);spec.loader.exec_module(safety)
        safety.require_independent_git(source)
        with tempfile.TemporaryDirectory(prefix="lyria-freeze-") as tmp:
            manifest=Path(tmp)/"paths.bin";manifest.write_bytes(b"".join(n.encode("utf-8")+b"\0" for n in files))
            git(source,"add","-f","--pathspec-from-file="+str(manifest),"--pathspec-file-nul")
        git(source,"-c","core.hooksPath="+str(source/".git"/"disabled-hooks"),"-c","commit.gpgSign=false","-c","user.name=Lyria local build","-c","user.email=lyria-build@localhost",
            "commit","-m","Freeze independent v0.24 candidate for local verification")
        result[edition]["commit"]=git(source,"rev-parse","HEAD").decode().strip()
        result[edition]["clean"]=not bool(git(source,"status","--porcelain").strip())
        if not result[edition]["clean"]:
            raise ValueError("Untracked content remains; inspect without git add --all")
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",required=True);p.add_argument("--baseline",required=True)
    p.add_argument("--extras",help="Reviewed JSON mapping edition to extra-file paths")
    p.add_argument("--execute",action="store_true")
    args=p.parse_args()
    extra=json.loads(Path(args.extras).read_text("utf-8")) if args.extras else {}
    if args.execute and not args.extras:p.error("--execute requires reviewed --extras")
    print(json.dumps(freeze(args.root,args.baseline,extra,args.execute),ensure_ascii=False,indent=2))
