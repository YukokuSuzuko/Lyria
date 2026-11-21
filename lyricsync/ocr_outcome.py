"""Content-free counts from the actual scan and downstream correspondence."""
from .model import valid_time
from .ocr_anchor_evidence import clipped_noise, single_crop
from .sequence_alignment import eligible


def scan_counts(project, rows):
    visuals = project.visuals
    metrics = project.video.get('ocr_metrics', {})
    roi = single_crop(project.settings)
    regions = project.settings.get('ocr_regions', [])
    counts = {
        'regions': sum(r.get('enabled', True) for r in regions) if project.settings.get('ocr_multi_region') else int(bool(roi)),
        'tracks': len(visuals),
        'fragments': sum(v.evidence.get('observation_status') == 'subline_fragment' for v in visuals),
        'short_uncertain': sum(v.evidence.get('observation_status') == 'short_uncertain' for v in visuals),
        'eligible': sum(eligible(v) and not clipped_noise(v, roi) for v in visuals),
        'timed': sum(bool(r['provenance']['visual_ids']) and valid_time(r['start'], r['end']) for r in rows),
    }
    if metrics and not metrics.get('complete_cache_hit'):
        for key, label in [('sampled_frames', 'samples'), ('accepted_text_regions', 'boxes')]:
            if type(metrics.get(key)) is int:
                counts[label] = metrics[key]
    return counts
