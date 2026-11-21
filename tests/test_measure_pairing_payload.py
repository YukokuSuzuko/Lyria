"""Baseline measurement is usable without Git and never executes supplied code."""
import subprocess
import pytest
from tools import measure_pairing_payload as measure


def test_source_baseline_ast_only_without_execution(tmp_path):
    root=tmp_path/'baseline';(root/'lyricsync').mkdir(parents=True)
    marker=tmp_path/'must-not-exist'
    (root/'lyricsync'/'api_alignment.py').write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nSYSTEM = 'original\\n系统'\nraise RuntimeError('never execute')\n",encoding='utf-8')
    text,origin=measure.load_baseline_system(baseline_source=root)
    assert text=='original\n系统' and 'AST' in origin
    assert not marker.exists()


def test_plain_text_exact_content(tmp_path):
    path=tmp_path/'prompt.txt';path.write_text('baseline\n系统\n',encoding='utf-8')
    assert measure.load_baseline_system(baseline_system=path)[0]=='baseline\n系统\n'


@pytest.mark.parametrize('source',["OTHER='x'", "SYSTEM=42", "SYSTEM=''", "SYSTEM=make_prompt()", "SYSTEM='a'\nSYSTEM='b'", 'invalid syntax !'])
def test_reject_nonliteral_missing_or_ambiguous_system(source):
    with pytest.raises(ValueError,match='literal SYSTEM'):measure.extract_system(source)


def test_git_fallback_and_explicit_baseline_origin(monkeypatch,tmp_path):
    calls=[]
    def git(args,**kwargs):
        calls.append(args);return "SYSTEM='git baseline'"
    monkeypatch.setattr(subprocess,'check_output',git)
    value,origin=measure.load_baseline_system(repository=tmp_path)
    assert value=='git baseline' and 'b65b395' in origin
    assert calls[0][1:3]==['-C',str(tmp_path)]


def test_no_git_fails_before_project_read_or_decode(monkeypatch,tmp_path,capsys):
    def missing(*args,**kwargs):raise FileNotFoundError('git unavailable')
    monkeypatch.setattr(subprocess,'check_output',missing)
    monkeypatch.setattr(measure.FrameIndex,'build',lambda *_:pytest.fail('must not decode'))
    output=tmp_path/'report.json'
    with pytest.raises(SystemExit) as exc:measure.main([str(tmp_path/'missing-project'),str(output)])
    assert exc.value.code==2
    assert '--baseline-source' in capsys.readouterr().err
    assert not output.exists()


def test_missing_explicit_baseline_does_not_fallback(monkeypatch,tmp_path):
    monkeypatch.setattr(subprocess,'check_output',lambda *_args,**_kwargs:pytest.fail('no fallback for bad explicit input'))
    with pytest.raises(ValueError,match='Baseline unavailable'):
        measure.load_baseline_system(baseline_source=tmp_path/'missing')


def test_conflicting_flags_rejected_before_io(tmp_path):
    with pytest.raises(SystemExit) as exc:
        measure.main(['project','output','--baseline-source','old','--baseline-system','old.txt'])
    assert exc.value.code==2
