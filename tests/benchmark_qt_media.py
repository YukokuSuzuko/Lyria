"""Finite CPU/offscreen Qt media diagnostic, not a pass/fail FPS benchmark.

Run from app/:
  ../.venv/bin/python tests/benchmark_qt_media.py --output ../validation/qt-media-1080p60
  # After optimization: add --fixture /path/to/frozen/synthetic-1080p60-long-gop.mp4

Generates a 3-second, 1080p60, no-audio, long-GOP fixture locally. Uses the real
Bridge/PyAV decode worker/QVideoSink without monkeypatching product code. Reports
observations, never Windows GPU/audio or end-to-end painted display FPS. Only
correctness (pause stability and final exact frame/PTS) has strict assertions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QUICK_BACKEND'] = 'software'
os.environ.setdefault('XDG_CACHE_HOME', '/tmp/lyria-media-benchmark-cache')
Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtWidgets import QApplication
import PySide6
import av
from desktop_qt.controller import Bridge
from lyricsync.audit_service import ffmpeg_executable


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def distribution(values):
    return {'sample_count': len(values), 'p50_ms': percentile(values, .5),
            'p95_ms': percentile(values, .95), 'max_ms': max(values) if values else None}


def until(predicate, timeout_ms=15000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer(); poll.setInterval(5)
    deadline = QTimer(); deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start(); deadline.start(timeout_ms)
    loop.exec()
    poll.stop(); deadline.stop()
    if not predicate():
        raise TimeoutError('bounded diagnostic timed out waiting for actual media state')


def delay(milliseconds):
    loop = QEventLoop()
    timer = QTimer(); timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setSingleShot(True); timer.timeout.connect(loop.quit)
    timer.start(milliseconds)
    loop.exec()


def run(output, fixture=None):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        'created_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
        'scope': 'CPU/offscreen real Qt Bridge + PyAV + QVideoSink; no audio; no QML scene painting',
        'not_acceptance_for': ['Windows hardware/GPU decode', 'real audio synchronization',
                               'physical display or end-to-end rendered FPS', 'long-session memory stability'],
        'environment': {'platform': platform.platform(), 'python': sys.version.split()[0],
                        'pyside6': PySide6.__version__, 'pyav': av.__version__,
                        'logical_cpu_count': os.cpu_count(), 'qpa': 'offscreen', 'quick_backend': 'software'},
        'fixture': {'width': 1920, 'height': 1080, 'fps': 60, 'frames': 180,
                    'seconds': 3, 'gop': 180, 'has_audio': False, 'kind': 'FFmpeg testsrc2 synthetic'},
        'notes': [
            'Request-to-presentation latency is product seekLatencyMs at accepted QVideoSink delivery, not photon latency.',
            'Decoded-frame metrics exclude the initial full-file PTS indexing pass; include decoder work/prefetch as last reported.',
            'Peak cache bytes is the largest product-reported RGB cacheBytes observed; excludes decoder buffers and image copies. Native-frame cache and combined observations are separately reported when available.',
            'GUI heartbeat is a 10ms precise QTimer on this event loop; no hard performance thresholds are imposed.',
            'Full-playback presentation counts include its exact initial seek and may include the final frame more than once.',
            'A short warm-up play/pause occurs before the complete playback measurement.',
            'Latency samples include only accepted presentations; superseded requests are not silently assigned zero latency.',
            'Each continuous scrub pattern issues 85 targets over at least 2 seconds, with a nominal 25ms gap.',
            'This shared cloud host has no CPU isolation; short-run observations are not a sustained real-time guarantee.'
        ]}
    video = fixture.resolve() if fixture is not None else output / 'synthetic-1080p60-long-gop.mp4'
    command = [ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
               'testsrc2=size=1920x1080:rate=60', '-frames:v', '180', '-an', '-c:v', 'libx264',
               '-preset', 'veryfast', '-g', '180', '-keyint_min', '180', '-sc_threshold', '0',
               '-pix_fmt', 'yuv420p', str(video)]
    encode_start = time.monotonic()
    if fixture is None:
        subprocess.run(command, check=True, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    elif not video.is_file():
        raise FileNotFoundError('Frozen fixture does not exist: ' + str(video))
    report['fixture']['generation_seconds'] = time.monotonic() - encode_start if fixture is None else 0.
    report['fixture']['reused_existing_file'] = fixture is not None
    report['fixture']['source_path'] = str(video)
    report['fixture']['sha256'] = hashlib.sha256(video.read_bytes()).hexdigest()
    app_root = Path(__file__).resolve().parents[1]
    report['code_sha256'] = {name: hashlib.sha256((app_root/name).read_bytes()).hexdigest()
                            for name in ['desktop_qt/media.py','desktop_qt/controller.py','tests/benchmark_qt_media.py']}
    report['fixture']['file_bytes'] = video.stat().st_size
    app = QApplication.instance() or QApplication([])
    bridge = Bridge(storage_dir=output / 'session-recovery')
    sink = QVideoSink(); bridge.attachVideoSink(sink)
    phase = ['indexing']; observations = []; heartbeat = []; errors = []
    last_tick = [time.monotonic()]; peak_cache = [0]
    monitor = QTimer(); monitor.setTimerType(Qt.TimerType.PreciseTimer); monitor.setInterval(10)
    def tick():
        now = time.monotonic()
        heartbeat.append({'phase': phase[0], 'interval_ms': (now-last_tick[0])*1000})
        last_tick[0] = now
    def presented(frame, stamp):
        state = bridge.media_controller.snapshot()
        observations.append({'phase': phase[0], 'frame': frame, 'pts': stamp,
                             'monotonic': time.monotonic(), 'latency_ms': state['seekLatencyMs'],
                             'decoder_seeks': state['decoderSeeks'], 'decoded_frames': state['decodedFrames'],
                             'cache_bytes': state['cacheBytes'],
                             'native_cache_bytes': state.get('nativeCacheBytes',0),
                             'native_cache_frames': state.get('nativeCacheFrames',0),
                             'worker_rgb_peak_bytes': state.get('rgbCachePeakBytes',0),
                             'worker_native_peak_bytes': state.get('nativeCachePeakBytes',0)})
        peak_cache[0] = max(peak_cache[0], state['cacheBytes'])
    monitor.timeout.connect(tick)
    bridge.media_controller.presented.connect(presented)
    bridge.media_controller.error.connect(errors.append)
    monitor.start()
    started = time.monotonic()
    try:
        assert bridge.command('media.open', {'path': str(video.resolve())})
        until(lambda: bridge.media['displayedFrame'] == 1 or bool(errors), 30000)
        if errors:
            raise RuntimeError(errors[-1])
        index = bridge.media_controller.index
        assert len(index.stamps) == 180 and not bridge.media_controller.metadata['has_audio']
        assert sink.videoFrame().isValid()
        report['indexing_to_first_presentation_seconds'] = time.monotonic() - started
        report['actual_frame_count'] = len(index.stamps)
        report['actual_duration_seconds'] = index.duration
        phase[0] = 'warmup_pause'
        assert bridge.command('media.play', {})
        until(lambda: bridge.media['displayedFrame'] >= 30, 10000)
        pause_started = time.monotonic()
        assert bridge.command('media.pause', {})
        pause_return_ms = (time.monotonic()-pause_started)*1000
        paused = bridge.media['displayedFrame']
        delay(200)
        pause_stable = (not bridge.media['playing'] and bridge.media['displayedFrame'] == paused
                        and bridge.media['requestedFrame'] == paused)
        assert pause_stable, 'a pending decoded frame moved the explicitly paused position'
        report['pause'] = {'actual_frame': paused, 'command_return_ms': pause_return_ms,
                           'stable_after_200ms': pause_stable}

        phase[0] = 'full_playback'
        before = bridge.media_controller.snapshot()
        playback_started = time.monotonic()
        assert bridge.command('media.seek', {'frame': 1})
        until(lambda: bridge.media['displayedFrame'] == 1)
        assert bridge.command('media.play', {})
        until(lambda: not bridge.media['playing'] and bridge.media['displayedFrame'] == 180, 12000)
        after = bridge.media_controller.snapshot()
        samples = [o for o in observations if o['phase'] == 'full_playback']
        unique = {o['frame'] for o in samples}
        report['full_playback'] = {
            'wall_seconds_including_initial_seek': time.monotonic()-playback_started,
            'accepted_presentation_events': len(samples), 'distinct_frames_presented': len(unique),
            'source_frames_not_observed_at_sink': len(index.stamps)-len(unique),
            'first_frame': min(unique), 'last_frame': max(unique),
            'decoder_seek_delta': after['decoderSeeks']-before['decoderSeeks'],
            'decoded_frame_delta': after['decodedFrames']-before['decodedFrames'],
            'request_to_sink_latency': distribution([o['latency_ms'] for o in samples]),
            'gui_10ms_timer_intervals': distribution([o['interval_ms'] for o in heartbeat if o['phase']=='full_playback'])}

        def scrub_diagnostic(name, targets, interval_ms=25):
            phase[0] = name
            before = bridge.media_controller.snapshot()
            input_times = [time.monotonic()]
            assert bridge.command('media.scrubBegin', {'frame': targets[0]})
            for target in targets[1:]:
                delay(interval_ms)
                input_times.append(time.monotonic())
                assert bridge.command('media.scrub', {'frame': target})
            during = [o for o in observations if o['phase']==name]
            final = targets[-1]
            already_displayed = bridge.media['displayedFrame'] == final
            final_started = time.monotonic()
            assert bridge.command('media.scrubEnd', {'frame': final})
            until(lambda: bridge.media['displayedFrame'] == final and not bridge.media['scrubbing'], 15000)
            final_completion_ms = (time.monotonic()-final_started)*1000
            exact_pts = round(index.stamp(final)*1_000_000)
            accurate = (bridge.media['requestedFrame'] == final and sink.videoFrame().startTime() == exact_pts)
            assert accurate, name + ' finished on a wrong real frame/PTS'
            delay(150)
            assert bridge.media['displayedFrame'] == bridge.media['requestedFrame'] == final
            samples = [o for o in observations if o['phase']==name]
            after = bridge.media_controller.snapshot()
            return {'requested_targets': targets, 'request_count': len(targets),
                'nominal_input_interval_ms': interval_ms,
                'input_duration_seconds': input_times[-1]-input_times[0],
                'actual_input_intervals': distribution([(b-a)*1000 for a,b in zip(input_times,input_times[1:])]),
                'presentations_before_release': len(during),
                'distinct_frames_before_release': len({o['frame'] for o in during}),
                'accepted_presentation_events': len(samples),
                'final_target_frame': final, 'actual_final_frame': bridge.media['displayedFrame'],
                'actual_sink_pts_us': sink.videoFrame().startTime(), 'expected_pts_us': exact_pts,
                'final_frame_already_displayed_at_release': already_displayed,
                'final_release_to_observed_completion_ms': final_completion_ms,
                'final_exact_and_stable': accurate,
                'decoder_seek_delta': after['decoderSeeks']-before['decoderSeeks'],
                'decoded_frame_delta': after['decodedFrames']-before['decodedFrames'],
                'observed_rgb_peak_bytes': max((o['cache_bytes'] for o in samples),default=0),
                'observed_native_peak_bytes': max((o['native_cache_bytes'] for o in samples),default=0),
                'observed_combined_cache_peak_bytes': max((o['cache_bytes']+o['native_cache_bytes'] for o in samples),default=0),
                'request_to_sink_latency': distribution([o['latency_ms'] for o in samples]),
                'during_drag_request_to_sink_latency': distribution([o['latency_ms'] for o in during]),
                'gui_10ms_timer_intervals': distribution([o['interval_ms'] for o in heartbeat if o['phase']==name])}

        rng = random.Random(20261030)
        report['random_scrub'] = scrub_diagnostic('random_scrub', [rng.randint(1,180) for _ in range(14)])
        report['random_scrub']['seed'] = 20261030
        # 85 points provide 84 x 25 ms = 2.1 seconds, leaving a safety margin.
        forward = [1+round(n*179/84) for n in range(85)]
        backward = list(reversed(forward))
        outward = [1+round(n*179/42) for n in range(43)]
        back_and_forth = outward + list(reversed(outward[:-1]))
        report['continuous_scrub'] = {}
        for name, targets in [('forward',forward),('backward',backward),('back_and_forth',back_and_forth)]:
            measured = scrub_diagnostic('continuous_'+name, targets)
            report['continuous_scrub'][name] = measured
            assert measured['input_duration_seconds'] >= 2, 'diagnostic did not sustain the requested input window'
        final_state = bridge.media_controller.snapshot()
        report['totals'] = {'accepted_presentation_events': len(observations),
                            'decoder_seek_count': final_state['decoderSeeks'],
                            'decoded_frames_last_reported': final_state['decodedFrames'],
                            'peak_reported_cache_bytes': peak_cache[0],
                            'peak_observed_native_cache_bytes': max((o['native_cache_bytes'] for o in observations),default=0),
                            'peak_observed_combined_cache_bytes': max((o['cache_bytes']+o['native_cache_bytes'] for o in observations),default=0),
                            'worker_reported_rgb_peak_bytes': final_state.get('rgbCachePeakBytes'),
                            'worker_reported_native_peak_bytes': final_state.get('nativeCachePeakBytes'),
                            'gui_10ms_timer_intervals_all_phases': distribution([o['interval_ms'] for o in heartbeat])}
        report['status'] = 'completed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        monitor.stop(); bridge.shutdown()
        try:
            until(lambda: not bridge.media_controller.worker.thread.is_alive(), 3000)
        except TimeoutError:
            report['worker_shutdown_timeout'] = True
        report['diagnostic_wall_seconds_excluding_generation'] = time.monotonic()-started
        report['media_errors'] = errors
        (output/'observations.json').write_text(json.dumps({'presentations': observations, 'gui_heartbeat': heartbeat}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('../validation/qt-media-1080p60'))
    parser.add_argument('--fixture', type=Path, help='Reuse an existing exact video without encoding or modifying it')
    args = parser.parse_args()
    run(args.output.resolve(), args.fixture)
