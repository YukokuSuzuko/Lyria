"""Opt-in multi-region OCR. The legacy single-region scanner stays unchanged.

The provider sees one enclosing crop per sampled frame and visits enabled regions
serially, avoiding one video decode per region and unbounded concurrent inference.
Region settings are part of its signature, so overlap and enabled-state changes
cannot reuse an incompatible scan cache. No project or timeline is mutated here.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
from .model import Detection, normalized

MAX_REGIONS = 4


def validate_regions(regions, width, height):
    if not isinstance(regions, list) or not 1 <= len(regions) <= MAX_REGIONS:
        raise ValueError('多选区需要 1 至 4 个区域。')
    result = []
    seen = set()
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError('多选区配置无效。')
        identity = region.get('id')
        box = region.get('box')
        if not isinstance(identity, str) or not identity or len(identity) > 64 or identity in seen:
            raise ValueError('选区标识缺失或重复。')
        if type(region.get('enabled', True)) is not bool:
            raise ValueError('选区启用状态无效。')
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) is not int for v in box)
                or not 0 <= box[0] < box[2] <= width
                or not 0 <= box[1] < box[3] <= height):
            raise ValueError('选区坐标必须为视频画面内的整数像素边界。')
        seen.add(identity)
        item = dict(id=identity, box=list(box), enabled=region.get('enabled', True))
        for key in ('name', 'speaker_name', 'speaker_id', 'style_id'):
            value = region.get(key, '')
            if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 for c in value):
                raise ValueError('选区名称或属性标识无效。')
            if value:
                item[key] = value
        result.append(item)
    if not any(r['enabled'] for r in result):
        raise ValueError('请至少启用一个识别区域。')
    return result


def bounding_box(regions):
    boxes = [r['box'] for r in regions if r['enabled']]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _duplicate(a, b):
    # Only nearly coincident, equal text is proof of a duplicate. Equal words
    # in different physical regions are independent observations.
    if not normalized(a.text) or normalized(a.text) != normalized(b.text):
        return False
    aa, bb = a.box, b.box
    overlap = max(0, min(aa[2], bb[2]) - max(aa[0], bb[0])) * max(0, min(aa[3], bb[3]) - max(aa[1], bb[1]))
    areas = [(r[2]-r[0])*(r[3]-r[1]) for r in (aa,bb)]
    return min(areas) > 0 and overlap >= .8 * min(areas) and overlap >= .5 * max(areas)


class RegionTrackers:
    """Drive separate tracker states with the same decoded frame and timestamp."""
    def __init__(self, regions, interval, threshold, max_gap):
        from .vision import Tracker
        self.trackers = {r['id']: Tracker(interval, threshold, max_gap) for r in regions}

    def feed(self, stamp, image, detections):
        for identity, tracker in self.trackers.items():
            tracker.feed(stamp, image, [d for d in detections if d.evidence['roi_owner'] == identity])

    def finish(self):
        return [track for tracker in self.trackers.values() for track in tracker.finish()]


def project_regions(settings, width, height):
    """Project an old pixel rectangle as a stable first region without writing the project."""
    if 'ocr_regions' in settings:
        return deepcopy(settings['ocr_regions'])
    box = settings.get('roi')
    if not box:
        return []
    return validate_regions([dict(id='legacy-roi-1', name='区域 1', box=list(box), enabled=True)], width, height)


def region_cards(project, width, height):
    if not width or not height:
        return []
    try:
        regions = project_regions(project.settings, width, height)
        if not regions:
            return []
        validate_regions([dict(r, enabled=True) for r in regions], width, height)
        from .styles import project_profiles
        styles = project_profiles(project)
        from .speakers import region_name
        result = []
        for i, r in enumerate(regions):
            x,y,u,v = r['box']
            speaker = region_name(project, r)
            style = r.get('style_id', '')
            name = r.get('name') or f'区域 {i+1}'
            label = ' · '.join(s for s in (name, speaker,
                               styles.get(style, {}).get('name', '') if style and style != 'default' else '') if s)
            result.append(dict(id=r['id'], paletteIndex=i, name=name, label=label, enabled=r.get('enabled',True),
                               speaker=speaker, style=style, x=x/width,y=y/height,width=(u-x)/width,height=(v-y)/height))
        return result
    except (KeyError, ValueError, TypeError):
        return []


def export_top(settings):
    if not settings.get('ocr_multi_region'):
        return None
    regions = [r for r in settings.get('ocr_regions', []) if isinstance(r, dict)
               and r.get('enabled', True) and isinstance(r.get('box'), (tuple, list)) and len(r['box']) == 4]
    if not regions:
        return None
    primary = next((r for r in regions if r.get('style_id', '') in ('', 'default', settings.get('current_subtitle_style','default'))), None)
    return (primary or max(regions, key=lambda r: r['box'][1]))['box'][1]


class RegionProvider:
    def __init__(self, provider, regions, cancel=lambda: False, cache=None):
        self.provider = provider
        self.regions = deepcopy([r for r in regions if r['enabled']])
        self.outer = bounding_box(self.regions)
        self.cancel = cancel
        self.cache = cache
        self.last_shape = {}
        self.metrics = {r['id']: {'model_calls': 0, 'crop_cache_hits': 0, 'static_shape_hits': 0} for r in self.regions}
        self.signature = {'multi_roi': 'v2-independent', 'provider': deepcopy(provider.signature),
                          'regions': [{'id': r['id'], 'box': r['box']} for r in self.regions]}

    def tracker_factory(self, interval, threshold, max_gap):
        return RegionTrackers(self.regions, interval, threshold, max_gap)

    def _crop_detections(self, region, crop):
        stat = self.metrics[region['id']]
        key = None
        if self.cache is not None:
            # Exact pixels protect short glyphs and contrast changes. A region edit
            # cannot invalidate another region, even for cached boundary probes.
            key = self.cache.key('ocr-region-pixels-v2', {'box': region['box'],
                        'ocr': self.provider.signature, 'pixels': hashlib.sha256(crop.tobytes()).hexdigest()})
            stored = self.cache.get(key)
            if stored is not None:
                stat['crop_cache_hits'] += 1
                return [Detection(**row) for row in stored]
        # Subtracting the origin pixel preserves every full-resolution edge and
        # punctuation pixel. Only a uniform one-level exposure change is reusable.
        # The extrema check rejects unsigned wrap-around and low-contrast images.
        import numpy as np
        pixels = np.ascontiguousarray(crop)
        origin = pixels[0, 0].astype(np.int16)
        shape = hashlib.sha256((pixels - pixels[0, 0]).tobytes()).digest()
        previous = self.last_shape.get(region['id'])
        if (previous and previous[0] == shape and np.all(np.abs(origin - previous[1]) <= 1)
                and int(pixels.min()) > 1 and int(pixels.max()) < 254
                and int(pixels.max()) - int(pixels.min()) >= 64):
            found = deepcopy(previous[2])
            stat['static_shape_hits'] += 1
        else:
            found = self.provider.recognize(crop)
            stat['model_calls'] += 1
            self.last_shape[region['id']] = (shape, origin, deepcopy(found))
        if key is not None:
            self.cache.put(key, [asdict(d) for d in found])
        return found

    def recognize_boundary(self, identity, frame):
        region = next(r for r in self.regions if r['id'] == identity)
        x, y, right, bottom = region['box']
        if self.cancel():
            raise InterruptedError('多选区 OCR 已取消。')
        result = []
        for item in self._crop_detections(region, frame[y:bottom, x:right]):
            bx, by, ex, ey = item.box
            result.append(replace(item, box=[x+bx, y+by, x+ex, y+ey],
                                  evidence={**item.evidence, 'roi_owner': identity, 'roi_ids': [identity]}))
        return result

    def recognize(self, image):
        detections: list[Detection] = []
        ox, oy = self.outer[:2]
        expected = (self.outer[3]-oy, self.outer[2]-ox)
        if image.shape[:2] != expected:
            raise ValueError('多选区输入画面尺寸与配置不一致。')
        for region in self.regions:
            if self.cancel():
                raise InterruptedError('多选区 OCR 已取消。')
            x1, y1, x2, y2 = region['box']
            for source in self._crop_detections(region, image[y1-oy:y2-oy, x1-ox:x2-ox]):
                evidence = deepcopy(source.evidence)
                evidence['roi_ids'] = [region['id']]
                evidence['roi_owner'] = region['id']
                bx, by, ex, ey = source.box
                margin = min(bx, by, x2 - x1 - ex, y2 - y1 - ey)
                evidence['roi_complete_box'] = margin > 1
                evidence['roi_inset'] = margin
                box = [source.box[0]+x1-ox, source.box[1]+y1-oy,
                       source.box[2]+x1-ox, source.box[3]+y1-oy]
                item = replace(source, box=box, evidence=evidence)
                duplicate = next((d for d in detections if region['id'] not in d.evidence['roi_ids'] and _duplicate(d, item)), None)
                if duplicate is None:
                    detections.append(item)
                else:
                    ids = list(dict.fromkeys(duplicate.evidence['roi_ids'] + [region['id']]))
                    def quality(d):
                        return (d.evidence['roi_complete_box'], d.confidence, d.evidence['roi_inset'])
                    if quality(item) > quality(duplicate):
                        detections[detections.index(duplicate)] = item
                        duplicate = item
                    duplicate.evidence['roi_ids'] = ids
        if self.cancel():
            raise InterruptedError('多选区 OCR 已取消。')
        return detections


def scan_regions(video_path, settings, cache, references=(), progress=lambda p,m: None,
                 cancel=lambda: False, on_checkpoint=lambda: None, *, provider=None):
    from .vision import Video, make_ocr, scan
    video = Video(video_path)
    regions = validate_regions(settings.get('ocr_regions'), video.info['width'], video.info['height'])
    options = deepcopy(settings)
    options['roi'] = bounding_box(regions)
    active = [r for r in regions if r['enabled']]
    base = provider or make_ocr(settings, references)
    wrapped = RegionProvider(base, regions, cancel, cache) if len(active) > 1 else None
    visuals, info = scan(video_path, options, cache, references, progress, cancel, on_checkpoint, provider=wrapped or base)
    if wrapped:
        info['ocr_region_metrics'] = wrapped.metrics
    # Store origin configuration with the result, not only mutable live settings.
    info['ocr_regions'] = deepcopy(regions)
    info['ocr_multi_region'] = True
    for visual in visuals:
        ids = []
        for observation in visual.evidence.get('components', []):
            for identity in observation.get('evidence', {}).get('roi_ids', []):
                if identity not in ids:
                    ids.append(identity)
        if len(active) == 1 and not ids:
            ids = [active[0]['id']]
        owners = [o['evidence']['roi_owner'] for o in visual.evidence.get('components', []) if o.get('evidence', {}).get('roi_owner')]
        visual.evidence['roi_owner'] = owners[0] if owners else (ids[0] if ids else '')
        visual.evidence['roi_ids'] = ids
        visual.evidence['roi_configuration'] = deepcopy(regions)
    return visuals, info


def rescan_settings(project, row, settings):
    """Prefer immutable source-region evidence over subsequently edited live ROIs."""
    origins: dict[str, dict] = {}
    referenced = [v for v in project.visuals if v.id in row.visual_ids]
    for visual in referenced:
        config = visual.evidence.get('roi_configuration')
        if not config:
            continue
        identities = visual.evidence.get('roi_ids') or [r['id'] for r in config if r.get('enabled', True)]
        for region in config:
            if region['id'] not in identities:
                continue
            saved = dict(deepcopy(region), enabled=True)
            if region['id'] in origins and origins[region['id']] != saved:
                raise ValueError('本句关联了位置不同的同名选区，请拆分或重新框选后整段识别。')
            origins[region['id']] = saved
    if origins:
        settings['ocr_multi_region'] = True
        settings['ocr_regions'] = list(origins.values())
    return settings
