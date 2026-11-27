"""Installer safety checks; all installation commands are intercepted."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from lyricsync import local_runtime_setup as setup
from lyricsync import local_alignment_runtime as runtime

def test_existing_target_rejected_before_any_command(tmp_path, monkeypatch):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    monkeypatch.setattr(setup, "_run", lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="已存在"):
        setup.create_runtime(sys.executable, target)
    assert (target / "keep.txt").read_text() == "keep"

def test_wrong_python_version_never_creates_target(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda p: SimpleNamespace(free=99 * 1024**3))
    monkeypatch.setattr(setup, "_run", lambda *a, **k: json.dumps({"version":[3,14],"bits":64}))
    target = tmp_path / "new"
    with pytest.raises(ValueError, match="Python 3.12"):
        setup.create_runtime(sys.executable, target)
    assert not target.exists()

def test_low_disk_never_runs_or_creates(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda p: SimpleNamespace(free=1024))
    monkeypatch.setattr(setup, "_run", lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="剩余空间不足"):
        setup.create_runtime(sys.executable, tmp_path / "new")

def test_failed_install_leaves_marked_new_env_without_deleting_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda p: SimpleNamespace(free=99 * 1024**3))
    def command(args, *rest, **kwargs):
        if "-c" in args:
            return json.dumps({"version":[3,12],"bits":64})
        raise RuntimeError("synthetic no install")
    monkeypatch.setattr(setup, "_run", command)
    target = tmp_path / "new"
    with pytest.raises(RuntimeError, match="synthetic"):
        setup.create_runtime(sys.executable, target)
    assert (target / "LYRIA_INSTALL_INCOMPLETE").exists()

@pytest.mark.parametrize("device", ["cpu", "cuda:0"])
def test_install_commands_only_official_binary_sources_and_new_target(tmp_path, monkeypatch, device):
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda p: SimpleNamespace(free=99 * 1024**3))
    commands = []
    def command(args, *rest, **kwargs):
        commands.append(args)
        return json.dumps({"version":[3,12],"bits":64}) if "-c" in args else ""
    monkeypatch.setattr(setup, "_run", command)
    monkeypatch.setattr(setup, "probe_runtime", lambda *a, **k: {"runtime":{"device":"cpu","test_only":True}})
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    # A path containing resume is not a pip resume option.
    target = tmp_path / "resume-target"
    result = setup.create_runtime(sys.executable, target, device=device)
    installs = [args for args in commands if "install" in args]
    assert len(installs) == 2
    assert all("--only-binary=:all:" in args and "--isolated" in args for args in installs)
    assert setup.TORCH_INDEXES[device] in installs[0]
    for args in installs:
        assert args[args.index("--timeout") + 1] == "120"
        assert args[args.index("--retries") + 1] == "2"
        assert sum(arg == "--timeout" for arg in args) == 1
        assert sum(arg == "--retries" for arg in args) == 1
        assert not any("resume" in arg.split("=", 1)[0] for arg in args if arg.startswith("--"))
    assert "https://pypi.org/simple" in installs[1]
    assert result["runtime_python"].startswith(str(target))
    assert not (target / "LYRIA_INSTALL_INCOMPLETE").exists()
    assert (target / "lyria-runtime.json").is_file()

def test_incomplete_and_system_site_runtime_is_rejected(tmp_path):
    target = tmp_path / "new"
    scripts = target / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_bytes(b"not executed")
    (target / "pyvenv.cfg").write_text("include-system-site-packages = false")
    marker = target / "LYRIA_INSTALL_INCOMPLETE"
    marker.write_text("incomplete")
    with pytest.raises(ValueError, match="未安装完整"):
        runtime.validate_runtime_path(python)
    marker.unlink()
    (target / "pyvenv.cfg").write_text("include-system-site-packages = true")
    with pytest.raises(ValueError, match="系统包继承"):
        runtime.validate_runtime_path(python)



def test_exited_installer_parent_with_inherited_pipe_reports_cleanup_not_success(tmp_path):
    """Real processes, no installation/network. Descendant self-exits at 2 s."""
    import threading
    import time
    child = "import time; time.sleep(2)"
    parent = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print('parent exited',flush=True)")
    cancelled = threading.Event()
    timer = threading.Timer(.2, cancelled.set)
    timer.start()
    began = time.monotonic()
    try:
        expected=InterruptedError if __import__('os').name=='nt' else RuntimeError
        with pytest.raises(expected):
            setup._run([sys.executable, "-c", parent], cancelled.is_set, lambda *a:None, 0, timeout=3)
        assert time.monotonic() - began < 1.5
    finally:
        timer.cancel()
        until = time.monotonic() + 4
        while any(t.name == "Lyria-installer-output" for t in threading.enumerate()) and time.monotonic() < until:
            time.sleep(.05)
        assert not any(t.name == "Lyria-installer-output" for t in threading.enumerate())


@pytest.mark.parametrize("setting", ["include-system-site-packages=true", "include-system-site-packages  =  true", "Include-System-Site-Packages = TRUE", "", "include-system-site-packages = maybe", "include-system-site-packages = false\ninclude-system-site-packages=true"])
def test_system_site_inheritance_parser_matches_python_not_fixed_spacing(tmp_path, setting):
    target=tmp_path/'env';scripts=target/'Scripts';scripts.mkdir(parents=True)
    python=scripts/'python.exe';python.write_bytes(b'not executed')
    (target/'pyvenv.cfg').write_text(setting)
    with pytest.raises(ValueError,match='系统包'):
        runtime.validate_runtime_path(python)
    (target/'pyvenv.cfg').write_text('include-system-site-packages=false')
    assert runtime.validate_runtime_path(python)==python
