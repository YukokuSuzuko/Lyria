"""Read-only real-media spectrum evidence; not an Aegisub or user acceptance."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lyricsync.spectrogram import (AudioSource, TileCache, RasterTileCache, time_grid_key,
                                  colourize, global_calibration, DISPLAY_PRESETS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--media', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    import av
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with av.open(args.media) as container:
        duration = float(container.duration / av.time_base)
    start = time.perf_counter()
    pcm = AudioSource.decode(args.media, duration)
    decode_seconds = time.perf_counter() - start
    try:
        spec = importlib.util.spec_from_file_location('dev9_spectrum_baseline', args.baseline)
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
        cache = TileCache()
        rasters = RasterTileCache()
        times = 37 + (np.arange(1280) + .5) / 1280 * 8
        def viewport(height):
            begin = time.perf_counter()
            result = []
            for index in range(5):
                columns = times[index*256:(index+1)*256]
                key = (index, time_grid_key(columns), height, 'detail', 512)
                def render():
                    values = cache.get(pcm, index, columns)
                    return colourize(values, pcm.reference, height, purple=True, **{
                        k: DISPLAY_PRESETS['detail'][k] for k in ('gamma','floor_db','ceiling_db')})
                result.append(rasters.get(pcm, key, render))
            rgb = np.concatenate(result, axis=1)
            return rgb, dict(seconds=time.perf_counter()-begin, fftTiles=cache.computations,
                             fftHits=cache.hits, rasterTiles=rasters.computations,
                             rasterHits=rasters.hits, fftBytes=cache.bytes, rasterBytes=rasters.bytes)
        benchmark = []
        images = []
        for name, height in [('cold',160), ('repeat',160), ('height_change',240), ('restore_height',160)]:
            rgb, metrics = viewport(height)
            benchmark.append(dict(name=name, height=height, **metrics))
            images.append(rgb)
        assert np.array_equal(images[0], images[1]) and np.array_equal(images[0], images[3])
        baseline_rgb = baseline.colourize(baseline.stft_columns(pcm,times), pcm.reference,160,
            purple=True,gamma=1.15,floor_db=-65.,ceiling_db=-5.)
        assert np.array_equal(images[0], baseline_rgb)
        rows = []
        parameters = []
        for window in (512,1024):
            values = cache.get(pcm, ('comparison', window), times, window=window)
            for preset_name, preset in DISPLAY_PRESETS.items():
                ref = global_calibration(pcm,window).reference if preset['quantile'] else pcm.reference
                image = colourize(values,ref,160,purple=True,**{
                    k: preset[k] for k in ('gamma','floor_db','ceiling_db')})
                strip = Image.new('RGB',(1280,190),(245,242,250))
                ImageDraw.Draw(strip).text((8,7),f'{preset_name}, Hann {window}/22050 s, FFT 2048, 37-45 s, 1280 physical columns',fill=(24,10,40))
                strip.paste(Image.fromarray(image),(0,30))
                rows.append(strip)
                parameters.append(dict(preset=preset_name,window=window,reference=ref))
        montage=Image.new('RGB',(1280,190*len(rows)),(255,255,255))
        for i,row in enumerate(rows): montage.paste(row,(0,i*190))
        montage.save(output/'0030-37-45-display-comparison.png')
        report=dict(scope='Real supplied media, direct renderer only; not native GUI/Aegisub/human onset acceptance',
                    media=args.media,durationSeconds=duration,excerptSeconds=[37,45],decodeSeconds=decode_seconds,
                    sameSettingsPixelIdenticalToDev9=True,benchmark=benchmark,parameters=parameters,
                    conclusions=['Repeated viewport and restored height reuse display tiles.',
                                 'New height reuses FFT; display mapping only.',
                                 'Default signal/palette unchanged; clarity is not declared fixed.',
                                 'Preset comparison is for human review; bright bins are not necessarily vocals.'])
        (output/'spectrum-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        pcm.close()

if __name__ == '__main__': main()