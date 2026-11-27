"""Reject borrowed worktree pointers before any build/freezer Git mutation."""
from pathlib import Path
import os
import subprocess

def require_independent_git(root):
    root=Path(root).resolve()
    meta=root/".git"
    if not meta.is_dir() or meta.is_symlink() or (hasattr(meta,"is_junction") and meta.is_junction()):
        raise ValueError("Expected independent .git directory, not a worktree pointer/link: "+str(root))
    if meta.resolve()!=root/".git":
        raise ValueError("Git metadata escapes source")
    if any(os.environ.get(k) for k in ("GIT_DIR","GIT_WORK_TREE","GIT_COMMON_DIR","GIT_INDEX_FILE","GIT_OBJECT_DIRECTORY","GIT_ALTERNATE_OBJECT_DIRECTORIES")):
        raise ValueError("Git redirection environment must be unset")
    def query(*args):
        return subprocess.check_output(["git","--no-optional-locks","-C",str(root),*args],
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)).decode("utf-8").strip()
    if Path(query("rev-parse","--absolute-git-dir")).resolve()!=meta.resolve():
        raise ValueError("Git metadata points to another repository")
    common=Path(query("rev-parse","--git-common-dir"))
    if not common.is_absolute():common=root/common
    if common.resolve()!=meta.resolve():
        raise ValueError("Shared Git common-dir is forbidden")
    if Path(query("rev-parse","--show-toplevel")).resolve()!=root:
        raise ValueError("Git worktree points outside source")
    records=query("worktree","list","--porcelain").splitlines()
    worktrees=[Path(line[9:]).resolve() for line in records if line.startswith("worktree ")]
    if worktrees!=[root]:
        raise ValueError("Only one independent worktree is permitted")
    if (meta/"objects"/"info"/"alternates").exists():
        raise ValueError("Borrowed Git object database is forbidden")
    return meta

if __name__=="__main__":
    import sys
    print(require_independent_git(sys.argv[1]))
