import threading
import pytest
from lyricsync import split_profiles as profiles

def test_corrupt_extension_preserves_cpu_and_never_runs_probe(tmp_path,monkeypatch):
    app=tmp_path/'v0.30'
    root=tmp_path/'accelerators/nvidia/shared-runtime'
    root.mkdir(parents=True);app.mkdir()
    calls=[]
    monkeypatch.setattr(profiles.shared,'application_dir',lambda:app)
    monkeypatch.setattr(profiles,'ACTIVE','cpu')
    monkeypatch.setattr(profiles,'REASON','')
    monkeypatch.setenv('LYRIA_ACCELERATOR','nvidia')
    monkeypatch.setattr(profiles,'run_probe',lambda *args,**kw:calls.append(args))
    profiles.configure(background=True)
    assert profiles.ACTIVE=='cpu' and profiles.REASON and not calls

def test_absent_acceleration_leaves_cpu(tmp_path,monkeypatch):
    app=tmp_path/'v0.30';app.mkdir()
    monkeypatch.setattr(profiles.shared,'application_dir',lambda:app)
    monkeypatch.setattr(profiles,'ACTIVE','cpu')
    monkeypatch.setenv('LYRIA_ACCELERATOR','auto')
    profiles.configure(background=True)
    assert profiles.ACTIVE=='cpu'

def test_background_is_nonblocking_and_cancel_recovers_cpu(monkeypatch):
    entered=threading.Event();done=threading.Event()
    monkeypatch.setattr(profiles,'_STOP',threading.Event())
    monkeypatch.setattr(profiles,'CHECKING',False)
    monkeypatch.setattr(profiles,'ACTIVE','cpu')
    def checking(*,background,cancel):
        assert background
        entered.set()
        while not cancel():done.wait(.01)
        done.set()
    monkeypatch.setattr(profiles,'configure',checking)
    profiles.start_background()
    assert entered.wait(2) and profiles.CHECKING and profiles.ACTIVE=='cpu'
    profiles.cancel_check()
    assert done.wait(2)

def test_background_hash_waits_for_foreground_ocr(monkeypatch):
    monkeypatch.setattr(profiles,'_STOP',threading.Event())
    event=threading.Event()
    def hash_checkpoint():
        profiles.verification_cancelled()
        event.set()
    with profiles.ocr_priority():
        worker=threading.Thread(target=hash_checkpoint)
        worker.start()
        assert not event.wait(.05)
    worker.join(1)
    assert event.is_set()
