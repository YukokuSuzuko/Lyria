"""Pure mock metadata safety; never initializes any real Git repository."""
from pathlib import Path
from unittest.mock import patch
import pytest
from tools.git_workspace_safety import require_independent_git

def answers(root, common=None, worktrees=None):
    def query(command, **kwargs):
        args=command[4:]
        if args==["rev-parse","--absolute-git-dir"]:value=str(root/".git")
        elif args==["rev-parse","--git-common-dir"]:value=str(common or root/".git")
        elif args==["rev-parse","--show-toplevel"]:value=str(root)
        elif args==["worktree","list","--porcelain"]:value="\n\n".join("worktree "+str(p) for p in (worktrees or [root]))
        else:raise AssertionError(args)
        return value.encode("utf-8")
    return query

def test_rejects_git_file_without_running_git(tmp_path):
    (tmp_path/".git").write_text("gitdir: D:/historical/.git/worktrees/source")
    with patch("tools.git_workspace_safety.subprocess.check_output") as query:
        with pytest.raises(ValueError,match="worktree pointer"):require_independent_git(tmp_path)
        query.assert_not_called()

def test_accepts_independent_metadata(tmp_path):
    (tmp_path/".git").mkdir()
    with patch.dict("os.environ",{},clear=True),patch("tools.git_workspace_safety.subprocess.check_output",side_effect=answers(tmp_path)):
        assert require_independent_git(tmp_path)==tmp_path/".git"

def test_rejects_shared_common_dir(tmp_path):
    (tmp_path/".git").mkdir()
    with patch.dict("os.environ",{},clear=True),patch("tools.git_workspace_safety.subprocess.check_output",side_effect=answers(tmp_path,tmp_path/"other")):
        with pytest.raises(ValueError,match="common-dir"):require_independent_git(tmp_path)

def test_rejects_registered_other_worktree(tmp_path):
    (tmp_path/".git").mkdir()
    with patch.dict("os.environ",{},clear=True),patch("tools.git_workspace_safety.subprocess.check_output",side_effect=answers(tmp_path,worktrees=[tmp_path,tmp_path/"other"])):
        with pytest.raises(ValueError,match="one independent"):require_independent_git(tmp_path)

def test_rejects_environment_redirect_before_git(tmp_path):
    (tmp_path/".git").mkdir()
    with patch.dict("os.environ",{"GIT_DIR":"elsewhere"},clear=True),patch("tools.git_workspace_safety.subprocess.check_output") as query:
        with pytest.raises(ValueError,match="redirection"):require_independent_git(tmp_path)
        query.assert_not_called()

def test_rejects_shared_objects(tmp_path):
    (tmp_path/".git"/"objects"/"info").mkdir(parents=True)
    (tmp_path/".git"/"objects"/"info"/"alternates").write_text("elsewhere")
    with patch.dict("os.environ",{},clear=True),patch("tools.git_workspace_safety.subprocess.check_output",side_effect=answers(tmp_path)):
        with pytest.raises(ValueError,match="object database"):require_independent_git(tmp_path)
