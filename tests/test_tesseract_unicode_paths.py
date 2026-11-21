from pathlib import Path
from unittest.mock import Mock
import os
import subprocess
import pytest
from lyricsync.tesseract_process import _TesseractProcesses,installed_tessdata


@pytest.mark.parametrize('method',['run','Popen','check_output'])
def test_unicode_data_relative_io_and_explicit_precedence(tmp_path,method):
    cwd=tmp_path/'原始 空间';cwd.mkdir()
    data=tmp_path/'选择 模型';data.mkdir()
    other=tmp_path/'错误 模型';other.mkdir()
    exe=tmp_path/'tesseract.exe';exe.touch()
    wrapped=Mock();proxy=_TesseractProcesses(wrapped)
    env={'TESSDATA_PREFIX':str(other),'KEEP':'yes'};before=dict(env)
    command=[str(exe),'输入.png','输出','--tessdata-dir',str(data),'-l','jpn','--psm','6','tsv']
    original=list(command)
    getattr(proxy,method)(command,cwd=str(cwd),env=env,creationflags=4)
    args,options=getattr(wrapped,method).call_args
    assert Path(options['cwd'])==data
    assert options['env']['TESSDATA_PREFIX']=='.' and options['env']['KEEP']=='yes'
    assert args[0][1:3]==[str(cwd/'输入.png'),str(cwd/'输出')]
    assert args[0][args[0].index('--tessdata-dir')+1]=='.'
    assert args[0][-5:]==['-l','jpn','--psm','6','tsv']
    assert env==before and command==original
    assert options['creationflags']==4|getattr(subprocess,'CREATE_NO_WINDOW',0)
    if method!='Popen':assert options['timeout']==10
    else:assert 'timeout' not in options


def test_env_relative_data_resolves_against_original_cwd(tmp_path):
    data=tmp_path/'资料';data.mkdir();wrapped=Mock()
    _TesseractProcesses(wrapped).run(['tesseract','stdin','stdout'],cwd=str(tmp_path),env={'TESSDATA_PREFIX':'资料'},timeout=3)
    command=wrapped.run.call_args.args[0];options=wrapped.run.call_args.kwargs
    assert command[1:]==['stdin','stdout']
    assert Path(options['cwd'])==data and options['timeout']==3


def test_missing_explicit_data_never_falls_back(tmp_path):
    existing=tmp_path/'data';existing.mkdir();wrapped=Mock()
    command=['tesseract','stdin','stdout','--tessdata-dir',str(tmp_path/'missing')]
    _TesseractProcesses(wrapped).run(command,env={'TESSDATA_PREFIX':str(existing)})
    assert wrapped.run.call_args.args[0]==command
    assert wrapped.run.call_args.kwargs.get('cwd') is None


def test_system_lookup_relative_report_resolved_to_child_cwd(tmp_path,monkeypatch):
    data=tmp_path/'系统 模型';data.mkdir()
    monkeypatch.setenv('TESSDATA_PREFIX',str(data))
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**k:'List of available languages in "./" (1):\neng\n')
    assert installed_tessdata('tesseract')==data


def test_named_option_values_not_mistaken_for_files_and_globals_unchanged(tmp_path,monkeypatch):
    data=tmp_path/'资料';data.mkdir();(tmp_path/'eng').touch();(tmp_path/'6').touch()
    config=tmp_path/'my config';config.touch();words=tmp_path/'words file';words.touch()
    before_cwd=os.getcwd();before_env=dict(os.environ);wrapped=Mock()
    _TesseractProcesses(wrapped).run(['tesseract','stdin','stdout','-l','eng','--psm','6','--user-words','words file','my config'],cwd=str(tmp_path),env={'TESSDATA_PREFIX':'资料'})
    command=wrapped.run.call_args.args[0]
    assert command[3:7]==['-l','eng','--psm','6']
    assert command[-3:]==['--user-words',str(words),str(config)]
    assert os.getcwd()==before_cwd and dict(os.environ)==before_env


def test_equals_data_and_relative_executable(tmp_path):
    data=tmp_path/'模型';data.mkdir();exe=tmp_path/'tool.exe';exe.touch()
    wrapped=Mock()
    _TesseractProcesses(wrapped).run(['tool.exe','stdin','stdout','--tessdata-dir=模型'],cwd=str(tmp_path))
    command=wrapped.run.call_args.args[0]
    assert command[0]==str(exe) and command[-1]=='--tessdata-dir=.'


def test_keyword_command_and_timeout_preserved(tmp_path):
    data=tmp_path/'data';data.mkdir();wrapped=Mock()
    _TesseractProcesses(wrapped).check_output(args=['tesseract','--list-langs'],env={'TESSDATA_PREFIX':str(data)},timeout=2)
    assert wrapped.check_output.call_args.kwargs['args']==['tesseract','--list-langs']
    assert wrapped.check_output.call_args.kwargs['timeout']==2


def test_nonbundled_system_default_unchanged(tmp_path):
    exe=tmp_path/'system-tesseract.exe';exe.touch();wrapped=Mock();env={'PATH':'fixed'}
    command=[str(exe),'--list-langs']
    _TesseractProcesses(wrapped).run(command,cwd=str(tmp_path),env=env)
    assert wrapped.run.call_args.args[0]==command
    assert wrapped.run.call_args.kwargs['cwd']==str(tmp_path)
    assert wrapped.run.call_args.kwargs['env'] is env


def test_adjacent_portable_data_and_quoted_explicit_directory(tmp_path):
    data=tmp_path/'tessdata';data.mkdir();exe=tmp_path/'tesseract.exe';exe.touch();wrapped=Mock()
    _TesseractProcesses(wrapped).run([str(exe),'--list-langs'],env={})
    assert wrapped.run.call_args.kwargs['cwd']==str(data)
    other=tmp_path/'别处 模型';other.mkdir()
    _TesseractProcesses(wrapped).run([str(exe),'stdin','stdout','--tessdata-dir','"'+str(other)+'"'],env={})
    assert wrapped.run.call_args.kwargs['cwd']==str(other)
