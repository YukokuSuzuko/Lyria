from desktop_qt.playback_clock import AudioPositionClock
import pytest


def test_coarse_position_interpolates_both_frames_and_never_runs_away():
    c=AudioPositionClock()
    assert c.sample(0,0)==0
    assert c.sample(0,.03)==0  # not yet confirmed advancing
    assert c.sample(.05,.05)==.05
    assert c.sample(.05,.07)==pytest.approx(.07)
    assert c.sample(.1,.1)==.1
    assert c.sample(.1,.14)==pytest.approx(.14)
    assert c.sample(.1,10)==pytest.approx(.175)  # stalled backend
    assert c.sample(.15,10.01)>=.175  # late reports cannot run video backwards


def test_seek_pause_and_resume_reset_prediction():
    c=AudioPositionClock();c.sample(1,0);c.sample(1.05,.05);c.sample(1.05,.09)
    c.reset()
    assert c.sample(.2,1)==.2
    assert c.sample(.25,1.05,False)==.25
    assert c.sample(.25,1.09,False)==.25
    c.reset()
    assert c.sample(8,2)==8
    assert c.sample(8.05,2.05)==8.05
    assert c.sample(8.05,2.08)==pytest.approx(8.08)
