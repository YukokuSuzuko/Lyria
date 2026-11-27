"""PTS video decoding, OCR providers and independent multiline visual tracks."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib
import math
import os
import shutil
import sys
import time

from .model import Detection, Visual, normalized, similarity, stable_id
from .storage import file_hash


def resources():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "resources"


class Video:
    def __init__(self, path):
        import av
        self.path = str(path)
        with av.open(self.path) as container:
            stream = container.streams.video[0]
            stream_start = float((stream.start_time or 0) * stream.time_base)
            self.origin = float(container.start_time/av.time_base) if container.start_time is not None else stream_start
            duration = float(stream.duration * stream.time_base) if stream.duration else (
                float(container.duration / av.time_base) if container.duration else 0)
            if stream.duration:
                duration += max(0, stream_start-self.origin)
            if not duration > 0:
                raise ValueError('无法读取视频时长，请检查视频文件是否完整。')
            self.info = {"width": stream.width, "height": stream.height, "duration": duration,
                         "fps": float(stream.average_rate or 30), "origin": self.origin,
                         "stream_start":stream_start,"time_base": str(stream.time_base),
                         "timestamp_source": "decoded PTS relative to container start (preserves video/audio lead)"}

    def frames(self, start=0, end=None, cancel=lambda: False):
        # Cancellation is cooperative: do not open/seek a stale request, and
        # stop as soon as an in-flight PyAV operation gives control back.
        if cancel():
            raise InterruptedError('任务已取消。')
        import av
        with av.open(self.path) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            if cancel():
                raise InterruptedError('任务已取消。')
            if start > 0:
                container.seek(max(0, int((start + self.origin) / stream.time_base)), stream=stream)
            if cancel():
                raise InterruptedError('任务已取消。')
            previous = -1.0
            for frame in container.decode(stream):
                if cancel():
                    raise InterruptedError('任务已取消。')
                if frame.pts is None:
                    raise ValueError('视频帧缺少时间戳，无法准确识别时间轴，请先转码为标准 MP4。')
                stamp = float(frame.pts * frame.time_base) - self.origin
                if stamp < start - 1e-6:
                    continue
                if end is not None and stamp > end + 1e-6:
                    break
                if stamp <= previous:
                    raise ValueError('视频时间戳顺序异常，请先转码为标准 MP4 后再试。')
                previous = stamp
                image = frame.to_ndarray(format="bgr24")
                if cancel():
                    raise InterruptedError('任务已取消。')
                yield stamp, image

    def at(self, stamp, cancel=lambda: False, last_pts=None):
        """Seek by PTS, using the decoded last PTS when an index is available.

        Without an index, retain the legacy end-of-file guard. A fixed .05s
        guard must not move indexed high-FPS/VFR tail frames backwards.
        """
        upper = self.info["duration"] - .05 if last_pts is None else last_pts
        frames = self.frames(max(0, min(stamp, upper)), cancel=cancel)
        try:
            return next(frames, (0, None))
        finally:
            # Do not rely on garbage collection to release each seek's decoder.
            frames.close()


def ocr_similarity(a, b):
    x, y = normalized(a), normalized(b)
    if not x or not y:
        return 0.0
    if min(len(x), len(y)) >= 3 and (x in y or y in x):
        return .68 + .32 * min(len(x), len(y)) / max(len(x), len(y))
    return similarity(a, b)


def intersection(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    return max(0, right-left) * max(0, bottom-top)


def box_overlap(a, b):
    return intersection(a, b) / max(1, min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1])))


class TesseractOCR:
    name = "tesseract"

    def __init__(self, language="jpn+eng", **_):
        import pytesseract
        from .tesseract_process import installed_tessdata, prepare_tesseract_processes
        prepare_tesseract_processes(pytesseract)
        root = resources() / "tesseract"
        candidates = [root / "tesseract.exe", Path(os.environ.get("LYRICSYNC_TESSERACT", "")),
                      Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")]
        executable = next((str(p) for p in candidates if p.is_file()), shutil.which("tesseract"))
        if not executable:
            raise RuntimeError('未找到内置 Tesseract 组件，请使用完整安装包。')
        pytesseract.pytesseract.tesseract_cmd = executable
        self.executable = executable
        data = Path(executable).parent / "tessdata"
        # pytesseract uses non-POSIX argument splitting on Windows. Embedding
        # quoted --tessdata-dir can retain literal quotes; use its native env.
        if data.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(data)
        else:
            data = installed_tessdata(executable)
        self.config = ""
        self.language = language
        installed = pytesseract.get_languages(config=self.config)
        missing = set(language.split("+")) - set(installed)
        if missing:
            raise ValueError("缺少 OCR 语言模型：" + ", ".join(sorted(missing)))
        self.signature = {"name": self.name, "version": str(pytesseract.get_tesseract_version()),
                          "lang": language,
                          "models": {n: file_hash(data / (n + ".traineddata")) for n in language.split("+")}}

    def recognize(self, image):
        import cv2
        import pytesseract
        original_image = image
        scale = min(2.0, max(1.0, 1100 / image.shape[1]))
        image = cv2.resize(image, None, fx=scale, fy=scale)
        data = pytesseract.image_to_data(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), lang=self.language,
                                         config=self.config + " --psm 11", output_type=pytesseract.Output.DICT,
                                         timeout=25)
        groups = {}
        for i, text in enumerate(data["text"]):
            if not normalized(text) or float(data["conf"][i]) < 20:
                continue
            key = tuple(data[k][i] for k in ("block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append(i)
        result = []
        for indexes in groups.values():
            indexes.sort(key=lambda i: data["left"][i])
            text = " ".join(data["text"][i] for i in indexes)
            box = [min(data["left"][i] for i in indexes), min(data["top"][i] for i in indexes),
                   max(data["left"][i]+data["width"][i] for i in indexes),
                   max(data["top"][i]+data["height"][i] for i in indexes)]
            result.append(Detection(text, [round(v / scale) for v in box],
                                    sum(float(data["conf"][i]) for i in indexes)/len(indexes)/100))
        from .ocr_spatial import tagged, spatial_lines
        return spatial_lines(tagged(result, 'tesseract'), original_image)


class RapidOCRProvider:
    name = "rapidocr"

    def __init__(self, language="jpn+eng", references=(), device='cpu', threads=2, model='ppocrv6', preprocess='subtitle', **_):
        self._settings = dict(language=language,references=references,device=device,threads=threads,model=model,preprocess=preprocess)
        try:
            self._initialize(**self._settings)
        except Exception as exc:
            if not self._backend_failure(exc):raise
            self._initialize(**dict(self._settings,device='cpu'))
            self.signature['fallback_reason']=str(exc)

    def _backend_failure(self, exc):
        return (getattr(sys,'frozen',False) and self._settings['device']!='cpu' and
            any(word in str(exc).lower() for word in ('executionprovider','execution provider','cuda','cudnn','directml','显卡','硬件加速','dml','gpu','device removed')))

    def _initialize(self,language,references,device,threads,model,preprocess):
        from rapidocr import RapidOCR, LangRec
        from importlib.metadata import version
        from .ocr_models import verified_v6_models
        from .ocr_preprocess import PREPROCESS_VERSION
        if model not in ("ppocrv6", "ppocrv4"):
            raise ValueError("未知 OCR 识字模型")
        if preprocess not in ("subtitle", "raw"):
            raise ValueError("未知 OCR 预处理方式")
        self.preprocess = preprocess
        import rapidocr
        from onnxruntime import get_available_providers, __version__ as ort_version
        available=get_available_providers()
        requested={'cpu':'CPUExecutionProvider','cuda':'CUDAExecutionProvider','directml':'DmlExecutionProvider'}
        if device=='auto':device=next((name for name in ('cuda','directml') if requested[name] in available),'cpu')
        original_device = device
        if getattr(sys,'frozen',False) and device in requested and requested[device] not in available:
            device='cpu'
        if device not in requested:raise ValueError('OCR 设备应为 cpu、cuda、directml 或 auto')
        if requested[device] not in available:raise RuntimeError('当前环境不支持 '+device+' 加速，请改用 CPU。可用后端：'+', '.join(available))
        if type(threads) is not int or not 1<=threads<=32:raise ValueError('OCR CPU 线程数应为 1～32')
        root = resources() / "models"
        package_models = Path(rapidocr.__file__).parent / "models"
        if not root.is_dir():
            root = package_models
        verified = verified_v6_models(root) if model == "ppocrv6" else {}
        detection_path = root / ("PP-OCRv6_det_small.onnx" if model == "ppocrv6" else "ch_PP-OCRv4_det_infer.onnx")
        # RapidOCR may otherwise download a missing explicit detector/classifier.
        # Offline distribution must fail before constructing any engine instead.
        for required in (detection_path, root / "ch_ppocr_mobile_v2.0_cls_infer.onnx"):
            if not required.is_file():
                raise RuntimeError("缺少离线 OCR 模型：" + required.name + "；不会自动下载，请使用完整安装包。")
        has_japanese = any(any("\u3040" <= c <= "\u30ff" or "\u3400" <= c <= "\u9fff" for c in t)
                           for t in references) if references else "jpn" in language
        if language in ("chi_sim", "zh", "chi_tra"):
            has_japanese = False
        has_latin = any(any("a" <= c.lower() <= "z" for c in t) for t in references) if references else "eng" in language
        self.engines = []
        params = {"Det.model_path": str(detection_path),
                  "Cls.model_path": str(root / "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
                  "Det.limit_type": "max", "Det.limit_side_len": 1280,
                  "Global.text_score": .4, "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                  "EngineConfig.onnxruntime.use_cuda": device=='cuda',
                  "EngineConfig.onnxruntime.use_dml": device=='directml',
                  "EngineConfig.onnxruntime.inter_op_num_threads": 1, "Global.log_level": "warning"}
        font_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = [Path(os.environ.get('LYRIA_OCR_FONT',''))]
        candidates += ([font_root/'msyh.ttc',font_root/'arial.ttf'] if sys.platform=='win32' else
                       [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
                        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')])
        font = next((p for p in candidates if p.is_file()), None)
        if font is None:
            raise RuntimeError('未找到可用的系统字体（可通过环境变量 LYRIA_OCR_FONT 指定字体路径）。')
        params["Global.font_path"] = str(font)
        paths = []
        if model == "ppocrv6":
            # The pinned ONNX embeds its multilingual character dictionary. The
            # explicit-path CTC/48px adapter is tested on RapidOCR 3.4.1; it does
            # not pretend that the old package registry knows the v6 family.
            paths.append(root / "PP-OCRv6_rec_medium.onnx")
        elif has_latin or not has_japanese:
            paths.append(root / "ch_PP-OCRv4_rec_infer.onnx")
        if has_japanese and model == "ppocrv4":
            japan = root / "japan_PP-OCRv4_rec_infer.onnx"
            if not japan.is_file():
                raise RuntimeError('缺少日文 RapidOCR 识别模型，请使用完整安装包。')
            paths.append(japan)
        for model_path in paths:
            if not model_path.is_file():
                raise RuntimeError("缺少离线 OCR 模型：" + model_path.name)
            self.engines.append(RapidOCR(params={**params, "Rec.model_path": str(model_path),
                "Rec.rec_img_shape": [3, 48, 320],
                "Rec.lang_type": LangRec.JAPAN if model_path.name.startswith("japan") else LangRec.CH}))
        self.references = list(references)
        sessions=[part.session.session for engine in self.engines for part in (engine.text_det,engine.text_rec)]
        actual={p for session in sessions for p in session.get_providers()}
        if not all(requested[device] in session.get_providers() for session in sessions):
            raise RuntimeError('OCR 硬件加速加载失败，请改用 CPU 或检查显卡驱动')
        if device=='directml' and any(session.get_session_options().enable_mem_pattern or
                str(session.get_session_options().execution_mode)!='ExecutionMode.ORT_SEQUENTIAL' for session in sessions):
            raise RuntimeError('DirectML 显卡加速初始化失败，请改用 CPU。')
        dictionary = {}
        if model == "ppocrv6":
            rec = self.engines[0].text_rec
            embedded = rec.session.session.get_modelmeta().custom_metadata_map.get("character", "")
            labels = rec.postprocess_op.character
            output_classes = rec.session.session.get_outputs()[0].shape[-1]
            if not embedded or "あ" not in embedded or "中" not in embedded or (isinstance(output_classes, int) and output_classes != len(labels)):
                raise RuntimeError("增强 OCR 内嵌字典/CTC 输出不匹配，拒绝使用错误字典识字。")
            dictionary = {"sha256": hashlib.sha256(embedded.encode("utf-8")).hexdigest(), "labels": len(labels), "output_classes": output_classes}
        self.signature = {"name": self.name, "version": version("rapidocr"), "models":
                          {p.name: verified.get(p.name) or file_hash(p) for p in paths + [detection_path]},
                          "model_profile": model, "preprocess": preprocess,
                          "preprocess_version": PREPROCESS_VERSION, "dictionary": dictionary,
                          "limit": 1280, "language": language,"device":device,"providers":sorted(actual),"threads":threads,
                          "runtime_version":ort_version}
        if original_device != device:
            self.signature["fallback_reason"] = f"{original_device} 不可用，本次 OCR 使用 CPU。"

    def recognize(self, image):
        try:
            return self._recognize(image)
        except Exception as exc:
            if self.signature['device']=='cpu' or not self._backend_failure(exc):raise
            self._initialize(**dict(self._settings,device='cpu'))
            self.signature['fallback_reason']=str(exc)
            return self._recognize(image)

    def _recognize(self, image):
        from .ocr_spatial import tagged, spatial_lines
        if self.preprocess == "raw":
            found = tagged(self._recognize_input(image), 'raw')
        else:
            from .ocr_preprocess import recover_clipped_subtitle, recover_empty_wide_subtitle
            lines = self._read_subtitle_crop(image)
            if not lines:
                return recover_empty_wide_subtitle(image, self._read_subtitle_crop)
            from dataclasses import replace
            height,width = image.shape[:2]
            for i,d in enumerate(lines):
                x1,y1,x2,y2 = d.box
                if (width < 6*height or d.confidence >= .85 or x2-x1 >= .4*width
                        or not (.35*width <= (x1+x2)/2 <= .65*width)
                        or not 2 <= len(normalized(d.text)) <= 8):
                    continue
                pad = round(.6*(y2-y1))
                left,right = max(0,x1-pad), min(width,x2+pad)
                retry = self._read_subtitle_crop(image[:,left:right])
                if len(retry) == 1:
                    alt = retry[0]
                    box = [alt.box[0]+left,alt.box[1],alt.box[2]+left,alt.box[3]]
                    if (alt.confidence >= max(.88,d.confidence+.08) and box_overlap(box,d.box) > .8
                            and ocr_similarity(alt.text,d.text) >= .65):
                        lines[i] = replace(d,text=alt.text,confidence=alt.confidence,
                            evidence=dict(d.evidence,tight_crop_reading=alt.text,tight_crop_prior=d.text))
            return recover_clipped_subtitle(image, lines, self._read_subtitle_crop)
        return spatial_lines(found, image, lambda box: self._recognize_spatial_line(image, box))

    def _read_subtitle_crop(self, image):
        from .ocr_spatial import tagged, spatial_lines
        from .ocr_preprocess import subtitle_image, needs_raw_recovery, recover_from_raw
        enhanced = tagged(self._recognize_input(subtitle_image(image)), 'minimum')
        found = (recover_from_raw(enhanced, tagged(self._recognize_input(image), 'raw'))
                 if needs_raw_recovery(enhanced) else enhanced)
        return spatial_lines(found, image, lambda box: self._recognize_spatial_line(image, box))

    def _recognize_spatial_line(self, image, box):
        """Read the actual union crop once; never replace it from reference lyrics."""
        from rapidocr.ch_ppocr_rec.typings import TextRecInput
        x1,y1,x2,y2 = box
        crop = image[max(0,y1-2):min(image.shape[0],y2+2),
                     max(0,x1-2):min(image.shape[1],x2+2)]
        if not crop.size or not getattr(self, 'engines', None):
            return None
        candidates=[]
        for engine in self.engines:
            result=engine.text_rec(TextRecInput(img=crop))
            if result.txts and result.scores:
                candidates.append((str(result.txts[0]),float(result.scores[0])))
        return max(candidates,key=lambda item:item[1],default=None)

    def _recognize_input(self, image):
        result = []
        for engine in self.engines:
            output = engine(image, use_cls=False)
            if output.txts is None:
                continue
            for text, confidence, polygon in zip(output.txts, output.scores, output.boxes):
                if len(normalized(text)) < 2:
                    continue
                box = [int(polygon[:, 0].min()), int(polygon[:, 1].min()),
                       int(polygon[:, 0].max()), int(polygon[:, 1].max())]
                candidate = Detection(text, box, float(confidence))
                duplicate = next((d for d in result if box_overlap(d.box, box) > .7), None)
                if duplicate:
                    def quality(d):
                        return .4*d.confidence + .6*max((ocr_similarity(d.text, r) for r in self.references), default=d.confidence)
                    if quality(candidate) > quality(duplicate):
                        result.remove(duplicate)
                        result.append(candidate)
                else:
                    result.append(candidate)
        return result


def role_ocr_settings(settings, role, language):
    """Explicit per-role override; existing shared engine remains the fallback."""
    if role not in ('chinese', 'source'):
        raise ValueError('未知的 OCR 识别区域类型')
    engine = settings.get(role + '_engine', settings.get('engine', 'rapidocr'))
    if engine not in ('rapidocr', 'tesseract'):
        raise ValueError('未知的 OCR 识别引擎')
    return {**settings, 'engine': engine, 'language': language}


def make_ocr(settings, references=()):
    kind = settings.get("engine", "rapidocr")
    if kind not in ("rapidocr", "tesseract"):
        raise ValueError("未知 OCR 引擎。")
    provider = RapidOCRProvider if kind == "rapidocr" else TesseractOCR
    if kind == 'rapidocr' and getattr(sys,'frozen',False):
        from .ocr_worker import IsolatedOCR
        provider = IsolatedOCR
    return provider(
        language=settings.get("language", "jpn+eng"), references=references,
        device=settings.get('ocr_device', 'auto' if kind == 'rapidocr' and getattr(sys,'frozen',False) else 'cpu'),
        threads=settings.get('ocr_threads',2),
        model=settings.get('ocr_model','ppocrv6'),preprocess=settings.get('ocr_preprocess','subtitle'))


def edge_mask(image):
    import cv2
    import numpy as np
    if image is None or image.size == 0:
        return np.zeros((1, 1), np.uint8)
    # Contrast-normalized gradient responds to shape rather than RGB highlight.
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    low, high = np.percentile(gray, (10, 99.8))
    if high-low < 7:
        high = float(gray.max())
    if high-low < 7:
        return np.zeros(gray.shape, np.uint8)
    gray = np.clip((gray.astype(np.float32)-low)*255/max(16, high-low), 0, 255).astype(np.uint8)
    return cv2.Canny(gray, 55, 145)


def patch(image, box, pad=2):
    h, w = image.shape[:2]
    x1,y1,x2,y2 = box
    return image[max(0,y1-pad):min(h,y2+pad), max(0,x1-pad):min(w,x2+pad)]


def visibility_score(template, image, box, radius=12):
    import cv2
    import numpy as np
    region = edge_mask(patch(image, box, radius+2))
    if template.size < 4 or np.count_nonzero(template) < 8:
        return 0.0
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return 0.0
    # Tolerate one pixel rasterization changes and small motion.
    reference = cv2.dilate(template, np.ones((3,3), np.uint8))
    candidate = cv2.dilate(region, np.ones((3,3), np.uint8))
    correlation = cv2.matchTemplate(candidate, reference, cv2.TM_CCORR_NORMED)
    _, score, _, location = cv2.minMaxLoc(correlation)
    x, y = location
    crop = candidate[y:y+reference.shape[0], x:x+reference.shape[1]]
    recall = np.count_nonzero((crop > 0) & (reference > 0)) / max(1, np.count_nonzero(reference))
    return float(min(score if math.isfinite(score) else 0, recall))


@dataclass
class Track:
    first: float
    last: float
    box: list[int]
    texts: list[str]
    confidences: list[float]
    template: object
    seed_time: float
    last_ocr: float
    misses: int = 0
    shape_hits: int = 0
    prior_absent: float = 0.0
    next_absent: float | None = None
    samples: list = field(default_factory=list)
    observations: list = field(default_factory=list)


class Tracker:
    def __init__(self, interval, threshold=.62, max_gap=.45):
        self.interval, self.threshold, self.max_gap = interval, threshold, max_gap
        self.active, self.finished = [], []
        self.previous = 0.0

    def feed(self, stamp, image, detections):
        # Expire before matching: identical text must not revive a track after
        # a gap by overwriting last with the current timestamp first.
        active=[]
        for tr in self.active:
            (self.finished if stamp-tr.last > max(self.max_gap,self.interval*1.5) else active).append(tr)
        self.active=active
        candidates = []
        from .ocr_spatial import contained_text, same_slot

        def same_region(a, b):
            # Match spatial_lines' separation rule: overlapping boxes or a gap
            # up to 2.5 median glyph heights share a region. Text equality alone
            # must not join distant captions at the same vertical position.
            horizontal_gap = max(0, max(a[0], b[0]) - min(a[2], b[2]))
            median_height = (max(1, a[3]-a[1]) + max(1, b[3]-b[1])) / 2
            return same_slot(a, b) and horizontal_gap <= 2.5 * median_height

        for ti, tr in enumerate(self.active):
            name = Counter(tr.texts).most_common(1)[0][0]
            for di, detection in enumerate(detections):
                sim = ocr_similarity(name, detection.text)
                spatial = box_overlap(tr.box, detection.box)
                if sim >= .62 and same_region(tr.box, detection.box):
                    candidates.append((.8*sim+.2*spatial, ti, di))
        used_tracks, used_detections = set(), set()
        for _, ti, di in sorted(candidates, reverse=True):
            if ti in used_tracks or di in used_detections:
                continue
            tr, d = self.active[ti], detections[di]
            whole = Counter(tr.texts).most_common(1)[0][0]
            is_part = contained_text(whole, tr.box, d.text, d.box, .85)
            tr.last = tr.last_ocr = stamp
            tr.texts.append(d.text)
            tr.confidences.append(d.confidence)
            tr.samples.append([stamp, "ocr", d.confidence])
            tr.observations.append(dict(pts=stamp, text=d.text, box=list(d.box),
                                        confidence=d.confidence, evidence=d.evidence))
            if is_part:
                for other_id, other in enumerate(detections):
                    if other_id in used_detections or other_id == di:
                        continue
                    if (not any(ci == other_id and ct != ti for _, ct, ci in candidates)
                            and contained_text(whole, tr.box, other.text, other.box, .85)
                            and (other.box[2] <= d.box[0] or other.box[0] >= d.box[2])):
                        tr.observations[-1].setdefault('subline_fragments', []).append(asdict(other))
                        used_detections.add(other_id)
            tr.misses = 0
            # Keep the first clear template stable, avoiding drift into background.
            if not is_part and d.confidence > max(tr.confidences[:-1], default=0) + .03:
                tr.box = d.box
                tr.template = edge_mask(patch(image, d.box))
                tr.seed_time = stamp
            used_tracks.add(ti)
            used_detections.add(di)
        keep = []
        for ti, tr in enumerate(self.active):
            visibly_absent=False
            if ti not in used_tracks:
                score = visibility_score(tr.template, image, tr.box)
                conflicting = any(same_region(tr.box, d.box) and
                                  ocr_similarity(Counter(tr.texts).most_common(1)[0][0], d.text) < .45
                                  for d in detections)
                # Shape alone is not fresh text evidence. Bound continuation from
                # the last OCR confirmation, not the last shape hit (which could
                # otherwise keep a background/repeated glyph alive indefinitely).
                shape_window=max(self.max_gap, self.interval*2)
                shape_expired=stamp-tr.last_ocr > shape_window
                if score >= self.threshold and not conflicting and not shape_expired:
                    tr.last = stamp
                    tr.shape_hits += 1
                    tr.misses = 0
                    tr.next_absent = None
                    tr.samples.append([stamp, "shape", round(score, 3)])
                else:
                    tr.misses += 1
                    # OCR dropout with a visible glyph shape is handled above.
                    # Strong negative visual evidence starts a new occurrence,
                    # even when identical lyrics return inside max_gap.
                    visibly_absent=conflicting or shape_expired or score < min(.3,self.threshold*.5)
                    if tr.next_absent is None:
                        tr.next_absent = stamp
            else:
                tr.next_absent = None
            if visibly_absent or stamp-tr.last > self.max_gap:
                self.finished.append(tr)
            else:
                keep.append(tr)
        self.active = keep
        for di, detection in enumerate(detections):
            if di not in used_detections:
                self.active.append(Track(stamp, stamp, detection.box, [detection.text],
                                         [detection.confidence], edge_mask(patch(image,detection.box)),
                                         stamp, stamp, prior_absent=self.previous,
                                         samples=[[stamp,"ocr",detection.confidence]],
                                         observations=[dict(pts=stamp,text=detection.text,
                                             box=list(detection.box),confidence=detection.confidence,
                                             evidence=detection.evidence)]))
        self.previous = stamp

    def finish(self):
        return self.finished + self.active


def text_presence(track, detections):
    """Tri-state evidence: OCR silence is not proof that a subtitle disappeared."""
    name = Counter(track.texts).most_common(1)[0][0]
    nearby = [d for d in detections if box_overlap(track.box, d.box) > .6]
    key = normalized(name)
    mean = sum(track.confidences) / max(1, len(track.confidences))
    floor = max(.70, mean-.05) if .70 <= mean < .85 else .85
    def same_reading(d):
        other = normalized(d.text)
        short_variant = (4 <= len(key) <= 6 and len(other) == len(key)
                         and box_overlap(track.box, d.box) > .75
                         and sum(a != b for a,b in zip(key,other)) == 1)
        return short_variant or ocr_similarity(name,d.text) >= .90
    if any(d.confidence >= floor and same_reading(d) and
           min(len(key), len(normalized(d.text))) / max(1, len(key), len(normalized(d.text))) >= .8
           for d in nearby):
        return True
    if any(d.confidence >= .85 and ocr_similarity(name, d.text) < .45 for d in nearby):
        return False
    return None


def refine_text_boundary(video, track, observations, coarse, boundary, probe, cancel):
    """Bounded local search within one occurrence's coarse bracket.

    Prefer bisection using text-confirmed replacement. For a protected local
    fallback, require both repeated OCR support and a clear shape drop; OCR
    silence on its own is never proof of disappearance.
    """
    if probe is None or len(observations) < 2:
        return None
    anchor = min(range(len(observations)), key=lambda i: abs(observations[i][0] - coarse))
    outside = 0 if boundary == 'start' else len(observations) - 1
    def present(index):
        if cancel():
            raise InterruptedError('识别已取消')
        stamp = observations[index][0]
        frames = video.frames(stamp, stamp + .000001, cancel)
        try:
            item = next(frames, None)
        finally:
            frames.close()
        if item is None:
            return None
        detections = probe(*item)
        if cancel():
            raise InterruptedError('识别已取消')
        return text_presence(track, detections)
    if anchor == outside or present(anchor) is not True:
        return None
    outside_state = present(outside)
    if outside_state is None:
        # A medium-confidence track can be supported by repeated, closely matching
        # OCR reads inside a high-correlation plateau and an actual adjacent shape
        # drop. OCR silence alone still does not count as disappearance.
        mean = sum(track.confidences) / max(1, len(track.confidences))
        anchor_score = observations[anchor][1]
        local = any(obs.get('evidence', {}).get('wide_empty_recovery_box') for obs in track.observations)
        # A confident short line can sit on persistent background edges. Still
        # require matching OCR on two separate frames and an adjacent shape drop.
        short = 2 <= len(normalized(Counter(track.texts).most_common(1)[0][0])) <= 8
        if (not .70 <= mean < .85 and not local and not (short and mean >= .85)) or anchor_score < .9:
            return None
        direction = -1 if boundary == 'start' else 1
        edge = anchor
        support = max(.84, anchor_score - .15)
        while edge != outside and observations[edge + direction][1] >= support:
            edge += direction
        if edge == outside or abs(edge - anchor) < 2:
            return None
        absent = edge + direction
        if (observations[absent][1] > min(.78, observations[edge][1] - .16)
                or present(edge) is not True or present(absent) is True):
            return None
        pair = sorted((observations[edge][0], observations[absent][0]))
        return (observations[edge if boundary == 'start' else absent][0], pair)
    if outside_state is not False:
        return None
    known, absent = anchor, outside
    # At most two endpoint probes + ten midpoints, independent of clip length.
    for _ in range(10):
        if abs(known - absent) == 1:
            pair = sorted((observations[known][0], observations[absent][0]))
            return (observations[known if boundary == 'start' else absent][0], pair)
        mid = (known + absent) // 2
        state = present(mid)
        if state is None:
            return None
        if state:
            known = mid
        else:
            absent = mid
    return None


def refine_track(video, track, interval, threshold, cancel, text_probe=None):
    """Inspect every decoded frame around both coarse brackets, never k timings."""
    notes = []
    first, last = track.first, track.last
    frame_step = 1/video.info["fps"]
    brackets = []
    def next_pts(stamp, observations):
        for pts, _ in observations:
            if pts > stamp + .000001:
                return pts
        for pts, _ in video.frames(stamp + .000001, video.info['duration'], cancel):
            if pts > stamp + .000001:
                return pts
        return video.info['duration']
    for boundary, coarse in (("start", first), ("end", last)):
        left, right = max(0, coarse-interval*1.6), min(video.info["duration"], coarse+interval*1.6)
        # Do not refine into another occurrence of identical text across an
        # already observed disappearance. Duration is not a split heuristic.
        if boundary == "start" and track.prior_absent is not None:
            left = max(left, track.prior_absent)
        if boundary == "end" and track.next_absent is not None:
            right = min(right, track.next_absent + frame_step)
        observations = [(t, visibility_score(track.template, frame, track.box))
                        for t, frame in video.frames(left, right, cancel)]
        visible = [i for i, (_, score) in enumerate(observations) if score >= threshold]
        if not visible:
            notes.append(f'{boundary} 进出点精查未检出清晰文字，暂保留粗扫时间')
            brackets.append([max(0, coarse-interval), min(video.info["duration"], coarse+interval)])
            if boundary == 'end':
                last = next_pts(track.last_ocr, observations)
            continue
        anchor = min(visible, key=lambda i: abs(observations[i][0]-coarse))
        edge = anchor
        direction = -1 if boundary == "start" else 1
        while 0 <= edge+direction < len(observations) and observations[edge+direction][1] >= threshold:
            edge += direction
        t = observations[edge][0]
        neighbor = edge+direction
        if 0 <= neighbor < len(observations):
            other = observations[neighbor][0]
            bracket = sorted((other,t))
        elif (boundary == "start" and left <= .000001) or (boundary == "end" and right >= video.info["duration"]-.001):
            bracket = [0.0, t] if boundary == "start" else [t, video.info["duration"]]
        else:
            confirmed = refine_text_boundary(video, track, observations, coarse, boundary, text_probe, cancel)
            if confirmed is not None:
                value, bracket = confirmed
                if boundary == 'start':
                    first = value
                else:
                    last = value
                brackets.append(bracket)
                notes.append(f'{boundary} 轮廓边界不明确，已按相邻帧文字变化复核')
                continue
            # Reaching the search edge is not a detected appearance/disappearance.
            # Retain observed coarse evidence, never extrapolate to the window edge.
            notes.append(f'{boundary} 超出局部核验范围，暂保留粗扫时间，请手动核对进出点')
            brackets.append([left, right])
            if boundary == 'end':
                last = next_pts(track.last_ocr, observations)
            continue
        near = [s for _, s in observations if threshold-.08 <= s <= threshold+.08]
        if len(near) >= 3:
            confirmed = refine_text_boundary(video, track, observations, coarse, boundary, text_probe, cancel)
            if confirmed is not None:
                value, bracket = confirmed
                if boundary == 'start':
                    first = value
                else:
                    last = value
                brackets.append(bracket)
                notes.append(f'{boundary} 已按相邻帧的字幕文字复核')
                continue
            notes.append(f'{boundary} 存在渐变或背景干扰，请留意核对进出点')
        brackets.append(bracket)
        # First frame above threshold; end is first frame below threshold.
        if boundary == "start":
            first = t
        else:
            last = next_pts(t, observations)
    if last <= first:
        notes.append('自动精修起止时间异常，请手动定位进出点')
        first, last = track.first, next_pts(track.last_ocr, observations)
    return first,last,brackets,notes


OCR_MIN_SAMPLE_FPS = 0.5
OCR_MAX_SAMPLE_FPS = 15.0
# Identical pixels may reuse OCR briefly, but shape similarity is not text proof.
OCR_REUSE_MAX_SECONDS = 1.0


def scan(video_path, settings, cache, references=(), progress=lambda p,m: None,
         cancel=lambda: False, on_checkpoint=lambda: None, *, provider=None):
    import cv2
    scan_started=time.monotonic()
    metrics={k:0.0 for k in ('hash_seconds','model_seconds','decode_seconds','preprocess_seconds',
                             'ocr_seconds','tracking_seconds','boundary_seconds','cache_seconds')}
    metrics.update(sampled_frames=0,decoded_frames=0,ocr_calls=0,cache_hits=0,reused_frames=0,accepted_text_regions=0,complete_cache_hit=False)
    video = Video(video_path)
    window=settings.get('scan_range') or [0,video.info['duration']]
    left,right=map(float,window)
    if not 0<=left<right<=video.info['duration']:raise ValueError('局部识别范围超出了视频总时长')
    fps = float(settings.get("sample_fps",3))
    threshold = float(settings.get("visibility",.62))
    if not OCR_MIN_SAMPLE_FPS <= fps <= OCR_MAX_SAMPLE_FPS or not .2 <= threshold <= .95:
        raise ValueError('OCR 扫描帧率需在 0.5～15 之间，清晰度阈值需在 0.2～0.95 之间。')
    roi = settings.get("roi") or [0,0,video.info["width"],video.info["height"]]
    if len(roi)!=4 or not (0<=roi[0]<roi[2]<=video.info["width"] and 0<=roi[1]<roi[3]<=video.info["height"]):
        raise ValueError('框选的字幕区域超出了视频画面范围。')
    progress(0,'正在校验视频文件与加载 OCR 模型…')
    tick=time.monotonic();digest = file_hash(video_path,cancel)
    metrics['hash_seconds']=time.monotonic()-tick
    video.info["content_hash"] = digest
    tick=time.monotonic();provider = provider or make_ocr(settings,references)
    metrics['model_seconds']=time.monotonic()-tick
    video.info['ocr_runtime']=provider.signature
    signature = {"algorithm": "visual-v3.4-verified-short-boundaries", "video":digest,"ocr":provider.signature,
                 "references":list(references),
                 "roi":roi,"fps":fps,"visibility":threshold,"max_gap":settings.get("max_gap",.45),
                 "range":[left,right],"rescan_token":settings.get('rescan_token')}
    completed_key = cache.key("scan",signature)
    completed = cache.get(completed_key)
    if completed is not None:
        metrics.update(complete_cache_hit=True,total_seconds=time.monotonic()-scan_started)
        video.info['ocr_metrics']=metrics
        progress(1,'已读取本地 OCR 缓存，无需重新扫描')
        return [Visual(**r) for r in completed],video.info
    tracker_class = getattr(provider, 'tracker_factory', Tracker)
    tracker = tracker_class(1/fps, threshold, float(settings.get("max_gap",.45)))
    interval, next_sample, started = 1/fps, left,time.monotonic()
    previous_detections, previous_signature = [],None
    last_inference = -math.inf
    decoded_stamps = []
    def measured_frames():
        iterator=video.frames(left,right,cancel=cancel)
        try:
            while True:
                tick=time.monotonic()
                try:item=next(iterator)
                except StopIteration:return
                finally:metrics['decode_seconds']+=time.monotonic()-tick
                metrics['decoded_frames']+=1
                decoded_stamps.append(item[0])
                yield item
        finally:iterator.close()
    def cached_get(key):
        tick=time.monotonic()
        try:return cache.get(key)
        finally:metrics['cache_seconds']+=time.monotonic()-tick
    def cached_put(key,value):
        tick=time.monotonic()
        try:cache.put(key,value)
        finally:metrics['cache_seconds']+=time.monotonic()-tick
    for stamp,frame in measured_frames():
        if stamp+1e-6 < next_sample:
            continue
        next_sample = stamp+interval-.5/video.info["fps"]
        x1,y1,x2,y2 = map(int,roi)
        crop = frame[y1:y2,x1:x2]
        frame_key = cache.key("ocr", {"scan":signature,"pts":round(stamp,6)})
        saved = cached_get(frame_key)
        tick=time.monotonic()
        image_signature = hashlib.sha256(crop.tobytes()).hexdigest()
        metrics['preprocess_seconds']+=time.monotonic()-tick
        if saved is not None:
            metrics['cache_hits']+=1
            detections = [Detection(**d) for d in saved]
        elif image_signature == previous_signature and stamp-last_inference < OCR_REUSE_MAX_SECONDS:
            # Only exact source pixels permit bounded reuse. Downsampled edge
            # equality can hide changed glyphs/punctuation or color contrasts.
            metrics['reused_frames']+=1
            detections = previous_detections
            cached_put(frame_key,[asdict(d) for d in detections])
        else:
            tick=time.monotonic();detections = provider.recognize(crop)
            metrics['ocr_seconds']+=time.monotonic()-tick;metrics['ocr_calls']+=1
            last_inference=stamp
            for d in detections:
                d.box = [d.box[0]+x1,d.box[1]+y1,d.box[2]+x1,d.box[3]+y1]
            cached_put(frame_key,[asdict(d) for d in detections])
        previous_signature,previous_detections = image_signature,detections
        metrics['accepted_text_regions']+=len(detections)
        tick=time.monotonic()
        tracker.feed(stamp,frame,detections)
        metrics['tracking_seconds']+=time.monotonic()-tick
        metrics['sampled_frames']+=1
        fraction = (stamp-left)/(right-left)
        elapsed=time.monotonic()-started
        remaining = elapsed/max(.001,fraction)*(1-fraction)
        eta=f'预计还需 {remaining:.0f} 秒' if fraction>=.05 and metrics['sampled_frames']>=3 else '正在估算剩余时间'
        progress(.75*fraction,f'画面初扫 {stamp - left:.1f}/{right - left:.1f} 秒 · 已用 {elapsed:.0f} 秒 · {eta}')
        on_checkpoint()
    tracks = tracker.finish()
    metrics['boundary_text_calls'] = 0
    metrics['boundary_text_cache_hits'] = 0
    boundary_detections = {}
    def boundary_probe(stamp, frame):
        # Share probes across nearby tracks; initial-scan cache uses the same ROI.
        token = round(stamp, 6)
        if token in boundary_detections:
            return boundary_detections[token]
        key = cache.key('ocr', {'scan': signature, 'pts': token})
        saved = cached_get(key)
        if saved is not None:
            metrics['boundary_text_cache_hits'] += 1
            found = [Detection(**d) for d in saved]
        else:
            x1,y1,x2,y2 = map(int,roi)
            tick = time.monotonic()
            found = provider.recognize(frame[y1:y2,x1:x2])
            metrics['ocr_seconds'] += time.monotonic() - tick
            metrics['ocr_calls'] += 1
            metrics['boundary_text_calls'] += 1
            for d in found:
                d.box = [d.box[0]+x1,d.box[1]+y1,d.box[2]+x1,d.box[3]+y1]
            cached_put(key, [asdict(d) for d in found])
        boundary_detections[token] = found
        return found
    visuals = []
    for index,tr in enumerate(tracks):
        if cancel():
            raise InterruptedError('识别已取消，已完成的识别进度已缓存。')
        name = Counter(tr.texts).most_common(1)[0][0]
        notes = []
        if len(tr.texts) < 2:
            notes.append('仅单帧识别出文字（可能是闪现字幕或画面误检）')
        if tr.shape_hits > len(tr.texts)*2:
            notes.append('文字中途较模糊，主要按轮廓推算持续时间，请核对')
        owner = tr.observations[0]['evidence'].get('roi_owner') if tr.observations else None
        refine_key = cache.key("boundary",{"scan":signature,"first":tr.first,"last":tr.last,"box":tr.box,"text":name,"seed":tr.seed_time})
        refined = cached_get(refine_key)
        if refined is None:
            tick=time.monotonic()
            probe = boundary_probe
            if owner and hasattr(provider, 'recognize_boundary'):
                def probe(stamp, frame, identity=owner):
                    token = (identity, round(stamp, 6))
                    if token not in boundary_detections:
                        started_probe = time.monotonic()
                        boundary_detections[token] = provider.recognize_boundary(identity, frame)
                        metrics['ocr_seconds'] += time.monotonic() - started_probe
                        metrics['boundary_text_calls'] += 1
                    else:
                        metrics['boundary_text_cache_hits'] += 1
                    return boundary_detections[token]
            a,b,brackets,boundary_notes = refine_track(video,tr,interval,threshold,cancel,probe)
            metrics['boundary_seconds']+=time.monotonic()-tick
            refined = [a,b,brackets,boundary_notes]
            cached_put(refine_key,refined)
        a,b,brackets,boundary_notes = refined
        notes.extend(boundary_notes)
        if references and max(ocr_similarity(name,r) for r in references) < .6:
            notes.append('识别文字与参考原文差异较大（可能是画面背景文字）')
        confidence = min(.98,sum(tr.confidences)/len(tr.confidences))
        if confidence < .85:
            notes.append('识字置信度偏低，请对照原画面核对文字；置信度不是准确率')
        if notes:
            confidence = min(confidence,.7)
        visuals.append(Visual(stable_id("visual",digest,round(tr.first,6),*tr.box),name,a,b,tr.box,
                              confidence,len(tr.texts),brackets[0],brackets[1],notes,
                              {"coarse_first":tr.first,"coarse_last":tr.last,"seed_time":tr.seed_time,
                               "shape_hits":tr.shape_hits,"samples":tr.samples,"visibility_threshold":threshold,
                               "line_slot":[tr.box[1],tr.box[3]],
                               "recognition_confidence":sum(tr.confidences)/len(tr.confidences),
                               "components":tr.observations,
                               "source_channels":list(dict.fromkeys(c for obs in tr.observations
                                   for c in obs['evidence'].get('source_channels',[]))),
                               "observation_status":('unresolved_multiline_block' if any(
                                   obs['evidence'].get('observation_status')=='unresolved_multiline_block'
                                   for obs in tr.observations) else 'normal'),
                               "eligible_for_alignment":not any(obs['evidence'].get('eligible_for_alignment') is False
                                   for obs in tr.observations),
                               "rule":"edge correlation/recall; unresolved window edges use bounded text replacement proof or coarse fallback; PTS"}))
        progress(.75+.25*(index+1)/max(1,len(tracks)),f'逐帧精修字幕进出点 {index + 1}/{len(tracks)}')
    visuals.sort(key=lambda v:(v.start,v.box[1]))
    if cancel():
        raise InterruptedError('识别已取消，已完成的识别进度已缓存。')
    from .ocr_spatial import classify_events
    if hasattr(provider, 'tracker_factory'):
        by_region = {}
        for visual in visuals:
            obs = visual.evidence['components']
            region_id = obs[0]['evidence'].get('roi_owner') if obs else None
            by_region.setdefault(region_id, []).append(visual)
        visuals = sorted((v for group in by_region.values() for v in classify_events(group, decoded_stamps, video.info['duration'])), key=lambda v:(v.start,v.box[1]))
    else:
        visuals = classify_events(visuals, decoded_stamps, video.info['duration'])
    cached_put(completed_key,[asdict(v) for v in visuals])
    metrics['total_seconds']=time.monotonic()-scan_started
    video.info['ocr_metrics']=metrics
    return visuals,video.info


def propose_regions(video_path, provider, cancel=lambda:False):
    video = Video(video_path)
    samples = []
    sampled = 0
    for fraction in (.03,.08,.12,.17,.23,.26,.36,.43,.50,.57,.61,.72,.79,.86,.92):
        if cancel():
            raise InterruptedError('已取消字幕区域自动检测')
        _, frame = video.at(video.info["duration"] * fraction)
        if frame is not None:
            sampled += 1
            samples.extend((fraction, d.box, d.text) for d in provider.recognize(frame) if normalized(d.text))
    return region_suggestions(samples, video.info["width"], video.info["height"], sample_count=sampled)


def region_suggestions(samples, width, height, *, sample_count=None):
    # Keep distant left/right subtitles separate even at the same height.
    samples = list(samples)
    if sample_count is None:
        sample_count = len({stamp for stamp, _, _ in samples})
    groups = []
    for stamp, box, text in samples:
        x1,y1,x2,y2 = box
        center = (y1+y2)/2
        group = next((g for g in groups if abs(center-g['y']) < height*.06 and
                     max(0, max(g['left'],x1)-min(g['right'],x2)) < width*.09),None)
        if group is None:
            group = dict(y=center,left=x1,right=x2,events=[])
            groups.append(group)
        group['events'].append((stamp,box,text))
        group['y']=sum((b[1]+b[3])/2 for _,b,_ in group['events']) / len(group['events'])
        group['left'],group['right']=min(x1,group['left']),max(x2,group['right'])
    result=[]
    for group in groups:
        events=group['events']
        boxes=[b for _,b,_ in events]
        examples=list(dict.fromkeys(text for _,_,text in events))
        x1,y1=min(b[0] for b in boxes),min(b[1] for b in boxes)
        x2,y2=max(b[2] for b in boxes),max(b[3] for b in boxes)
        corner=(x2<width*.25 or x1>width*.75) and (y2<height*.25 or y1>height*.75)
        fixed=all(max(b[i] for b in boxes)-min(b[i] for b in boxes)<=max(3,min(width,height)*.01) for i in range(4))
        watermark=(corner and fixed and len(examples)==1 and len(normalized(examples[0]))<=5
                   and len({stamp for stamp,_,_ in events})>=4)
        stamps = {stamp for stamp,_,_ in events}
        static_number = False
        if (len(examples)==1 and normalized(examples[0]).isdecimal()
                and len(normalized(examples[0]))<=3 and len(stamps)==len(events)
                and len(stamps) >= max(4, math.ceil(.75*sample_count))):
            # Detector boxes can breathe around one motionless glyph. Require
            # tight center spread and substantial pairwise IoU so position
            # changes, duplicate lanes and countdowns remain real candidates.
            jitter = max(3, min(width,height)*.01)
            cx = [(b[0]+b[2])/2 for b in boxes]
            cy = [(b[1]+b[3])/2 for b in boxes]
            def occupancy(a,b):
                shared = intersection(a,b)
                area = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1])
                return shared / max(1, area-shared)
            static_number = (max(cx)-min(cx)<=jitter and max(cy)-min(cy)<=jitter
                             and all(occupancy(a,b)>=.75 for i,a in enumerate(boxes) for b in boxes[i+1:]))
        # Symmetric padding is for a clearly centered, horizontally extensive
        # subtitle group, not off-center credits or separate left/right lanes.
        wide = [b for b in boxes if b[2]-b[0] >= width*.30]
        centered = [b for b in wide if abs((b[0]+b[2])/2-width/2) <= width*.085]
        if len(centered) >= 3 and len(centered) >= .80*len(wide):
            x1, x2 = min(x1, width-x2), max(x2, width-x1)
        padded = [max(0,x1-30),max(0,y1-30),min(width,x2+30),min(height,y2+30)]
        # Full-frame inference can truncate even symmetrically centered samples.
        # Reserve lateral safety room only for a consistently centered lower
        # subtitle band with several broad text reads, not short/off-center lanes.
        if y1 >= height*.73 and len(centered) >= 3 and len(centered) >= .80*len(wide):
            padded[0] = min(padded[0], round(width*.14))
            padded[2] = max(padded[2], round(width*.86))
        result.append(dict(box=padded,
                      hits=len(boxes), distinct=len({normalized(t) for t in examples}), examples=examples[:3], watermark=bool(watermark), static_number=static_number,
                      reason='疑似固定角标，可手动选择' if watermark else '疑似固定数字，可手动选择' if static_number else '检测到文字带，请确认'))
    return sorted(result,key=lambda r:(r['watermark'] or r['static_number'],-r['distinct'],-r['hits']))
