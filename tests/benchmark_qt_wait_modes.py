"""Compare diagnostic wait strategies without changing the media decoder.

Run: ../.venv/bin/python tests/benchmark_qt_wait_modes.py --output NEW_DIRECTORY

Locally generates silent H.264 29.97, 1080p60 long-GOP, and VFR fixtures, then
uses the unchanged MediaController/PyAV/QVideoSink. Repeated QTest.qWait matches
the old responsiveness harness; QEventLoop matches normal QApplication.exec
scheduling. This diagnoses a test-driver artifact, not Windows performance.
Timing has no pass/fail threshold. Actual RGB pixels, PTS, final exact frame,
and worker shutdown are strictly checked in both modes. Intermediate scrub
presentation is required in the corrected event-loop mode; the legacy mode
records starvation rather than aborting the comparison it aims to reproduce.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ.setdefault('XDG_CACHE_HOME', '/tmp/lyria-wait-benchmark-cache')
Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import av
import PySide6
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop_qt.media import MediaController
from lyricsync.audit_service import ffmpeg_executable
from tools.qt_event_loop_wait import delay, wait_until


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    values = sorted(values)
    return {'samples': len(values), 'p50_ms': values[len(values)//2],
            'p95_ms': values[min(len(values)-1, int(len(values)*.95))],
            'max_ms': max(values)}


def legacy_wait(app, predicate, timeout_ms=15000):
    deadline = time.monotonic() + timeout_ms/1000
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise TimeoutError('Actual frame did not arrive in legacy QTest wait')


def python_worker_probe():
    stop = threading.Event()
    counter = [0]

    def work():
        while not stop.is_set():
            counter[0] += 1
            time.sleep(.001)

    worker = threading.Thread(target=work)
    worker.start()
    rows = []
    try:
        for mode in ('qtest', 'event_loop', 'qtest', 'event_loop'):
            before = counter[0]
            started = time.monotonic()
            if mode == 'qtest':
                for _ in range(40):
                    QApplication.processEvents()
                    QTest.qWait(5)
            else:
                delay(200)
            rows.append({'mode': mode, 'elapsed_ms': (time.monotonic()-started)*1000,
                         'worker_iterations': counter[0]-before})
    finally:
        stop.set()
        worker.join(2)
    assert not worker.is_alive()
    return rows


def make_fixtures(output):
    specs = [('2997', '640x360', '30000/1001', 90, None),
             ('1080p60_long_gop', '1920x1080', '60', 180, None),
             ('vfr', '640x360', '60', 90, 'select=not(mod(n\\,2))+not(mod(n\\,5))')]
    fixtures = []
    for name, size, rate, gop, select in specs:
        path = output / (name + '.mp4')
        command = [str(ffmpeg_executable()), '-hide_banner', '-loglevel', 'error',
                   '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}']
        if select:
            command += ['-vf', select, '-fps_mode', 'vfr']
        command += ['-frames:v', '180', '-an', '-c:v', 'libx264', '-preset', 'veryfast',
                    '-g', str(gop), '-keyint_min', str(gop), '-sc_threshold', '0',
                    '-pix_fmt', 'yuv420p', str(path)]
        subprocess.run(command, check=True, timeout=90, capture_output=True)
        fixtures.append((name, path, command))
    return fixtures


def reference(path):
    pts, pixels = [], []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = 'AUTO'
        for frame in container.decode(stream):
            pts.append(float(frame.pts * frame.time_base))
            pixels.append(hashlib.sha256(frame.to_ndarray(format='rgb24').tobytes()).hexdigest())
    return pts, pixels


def run_mode(app, path, mode, reference_pts, reference_pixels):
    media = MediaController()
    sink = QVideoSink()
    media.attach_sink(sink)
    errors, observations = [], []
    media.error.connect(errors.append)
    media.presented.connect(lambda frame, stamp: observations.append(
        {'frame': frame, 'stamp': stamp, 'latency_ms': media.snapshot()['seekLatencyMs']}))
    wait = (lambda predicate: legacy_wait(app, predicate)) if mode == 'qtest' else wait_until
    idle = QTest.qWait if mode == 'qtest' else delay

    def check(frame):
        state = media.snapshot()
        assert state['displayedFrame'] == frame
        assert state['requestedFrame'] == frame
        assert sink.videoFrame().startTime() == round(reference_pts[frame-1]*1_000_000)
        image = sink.videoFrame().toImage().convertToFormat(QImage.Format.Format_RGB888)
        # All fixture widths have no RGB row padding.
        assert image.bytesPerLine() == image.width()*3
        actual = hashlib.sha256(bytes(image.constBits())).hexdigest()
        assert actual == reference_pixels[frame-1], 'requested label is not actual decoded RGB'
        assert not errors, errors

    try:
        media.open(str(path))
        # Keep indexing setup identical, outside the wait-mode measurements.
        wait_until(lambda: media.snapshot()['displayedFrame'] == 1 or bool(errors), timeout_ms=30000)
        check(1)
        delay(50)
        count = len(reference_pts)
        targets = [41, 150, 70, 170, 45, count, 1]
        rows = []
        for target in targets:
            before = media.snapshot()
            started = time.monotonic()
            media.seek(target)
            # Count a new accepted presentation; matching an already displayed
            # frame alone could accidentally report a stale latency as a sample.
            wait(lambda: media.snapshot()['presentedFrames'] > before['presentedFrames']
                 and media.snapshot()['displayedFrame'] == target or bool(errors))
            check(target)
            after = media.snapshot()
            rows.append({'target': target, 'elapsed_ms': (time.monotonic()-started)*1000,
                         'request_to_sink_ms': after['seekLatencyMs'],
                         'decoded_frames': after['decodedFrames']-before['decodedFrames'],
                         'decoder_seeks': after['decoderSeeks']-before['decoderSeeks']})
        # Preserve real intermediate image presentation during a continuous
        # two-second gesture, then demand a new exact release generation.
        before = media.snapshot()
        first_observation = len(observations)
        started = time.monotonic()
        targets = [1+round(n*(count-1)/42) for n in range(43)]
        targets += list(reversed(targets[:-1]))
        media.scrub(targets[0], begin=True)
        for target in targets[1:]:
            idle(25)
            media.scrub(target)
        during = observations[first_observation:]
        input_duration = time.monotonic()-started
        assert input_duration >= 2
        final_target = targets[-1]
        final_count = media.snapshot()['presentedFrames']
        released = time.monotonic()
        media.seek(final_target)
        wait(lambda: media.snapshot()['presentedFrames'] > final_count
             and media.snapshot()['displayedFrame'] == final_target or bool(errors))
        completion_ms = (time.monotonic()-released)*1000
        check(final_target)
        delay(100)
        check(final_target)
        after = media.snapshot()
        return {'mode': mode, 'seek_samples': rows,
                'seek_request_to_sink': distribution([row['request_to_sink_ms'] for row in rows]),
                'scrub': {'input_duration_seconds': input_duration, 'requests': len(targets),
                          'presentations_before_release': len(during),
                          'intermediate_presentations_sufficient': len(during) >= 3,
                          'distinct_frames_before_release': len({x['frame'] for x in during}),
                          'release_to_exact_ms': completion_ms, 'final_frame': final_target,
                          'exact_rgb_pts_and_stable': True,
                          'decoder_seeks': after['decoderSeeks']-before['decoderSeeks'],
                          'decoded_frames': after['decodedFrames']-before['decodedFrames']},
                'actual_frame_count': count, 'vfr': media.metadata['vfr'],
                'media_errors': errors}
    finally:
        media.close()
        media.worker.thread.join(3)
        assert not media.worker.thread.is_alive(), 'decode worker did not shut down'
        media.deleteLater()
        app.processEvents()


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    sources = ['desktop_qt/media.py', 'tools/qt_event_loop_wait.py',
               'desktop_qt/qml/tests/run_qml_responsiveness.py', 'tests/benchmark_qt_wait_modes.py']
    hashes = {name: digest(ROOT/name) for name in sources}
    report = {'status': 'running', 'created_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Linux CPU/offscreen QVideoSink diagnostic wait comparison, not a decoder optimization',
              'windows_acceptance': False, 'performance_acceptance': False,
              'environment': {'platform': platform.platform(), 'python': sys.version,
                              'pyside': PySide6.__version__, 'pyav': av.__version__, 'cpu_count': os.cpu_count()},
              'code_sha256': hashes, 'fixtures': [],
              'notes': ['No user media, audio, external API or upload.',
                        'Pixel/PTS oracle is a separate sequential PyAV decode.',
                        'Both modes use the same unchanged production decoder and exact fixture bytes.',
                        'Wait modes alternate order across fixtures to expose ordering bias.',
                        'Cloud host is shared; this is not isolated sustained throughput or display latency.']}
    app = QApplication.instance() or QApplication([])
    try:
        report['python_worker_probe'] = python_worker_probe()
        for n, (name, path, command) in enumerate(make_fixtures(output)):
            pts, pixels = reference(path)
            item = {'name': name, 'path': str(path), 'sha256': digest(path), 'generation_command': command,
                    'decoded_reference_pts': pts, 'modes': []}
            report['fixtures'].append(item)
            modes = ('qtest', 'event_loop') if n % 2 == 0 else ('event_loop', 'qtest')
            for mode in modes:
                row = run_mode(app, path, mode, pts, pixels)
                item['modes'].append(row)
                print(name, mode, json.dumps(row['seek_request_to_sink']), json.dumps(row['scrub']), flush=True)
                (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
                if mode == 'event_loop':
                    assert row['scrub']['intermediate_presentations_sufficient'], 'corrected wait starved actual intermediate frames'
        report['status'] = 'completed'
    except BaseException as exc:
        report.update(status='failed', error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        report['source_unchanged'] = hashes == {name: digest(ROOT/name) for name in sources}
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.output.resolve())
