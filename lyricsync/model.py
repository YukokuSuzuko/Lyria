from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import unicodedata
from .timebase import Timebase, TIME_EPSILON, valid_time, validate_boundaries

TEXT_ROLES = ('translation', 'source_ocr', 'source_asr', 'source_import')


def text_role_label(role):
    return {'translation': '译文', 'source_ocr': 'OCR 原文',
            'source_asr': 'ASR 转写原文', 'source_import': '导入原文'}[role]



def active_at(start, end, stamp):
    return start <= stamp + TIME_EPSILON and end > stamp + TIME_EPSILON


def stable_id(namespace: str, *parts) -> str:
    value = "\x1f".join(map(str, parts)).encode("utf-8")
    return namespace + "-" + hashlib.sha256(value).hexdigest()[:20]


def normalized(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c)[0] in "LN")


def similarity(a: str, b: str) -> float:
    a, b = normalized(a), normalized(b)
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0



@dataclass
class Lyric:
    id: str
    text: str
    start: float | None
    end: float | None
    event_ids: list[str] = field(default_factory=list)
    position: list[float] | None = None
    warnings: list[str] = field(default_factory=list)

    reading: str = ""
    reading_segments: list[dict] = field(default_factory=list)
    # Immutable imported Actor/named Style evidence, separate from editable rows.
    subtitle_metadata: dict = field(default_factory=dict)


    @property
    def is_interjection(self):
        from .translation_alignment import interjection
        return interjection(self.text)


@dataclass
class Translation:
    id: str
    text: str
    start: float | None = None
    end: float | None = None
    reference: str | None = None
    subtitle_metadata: dict = field(default_factory=dict)


@dataclass
class Detection:
    text: str
    box: list[int]
    confidence: float
    evidence: dict = field(default_factory=dict)


@dataclass
class Visual:
    id: str
    text: str
    start: float
    end: float
    box: list[int]
    confidence: float
    hits: int
    start_bracket: list[float] = field(default_factory=list)
    end_bracket: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


@dataclass
class Mapping:
    id: str
    translation_ids: list[str]
    lyric_ids: list[str] = field(default_factory=list)
    visual_ids: list[str] = field(default_factory=list)
    start: float | None = None
    end: float | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    locked: bool = False
    confirmed: bool = False
    ignored: bool = False
    method: str = "local"
    text_segments: list[dict] = field(default_factory=list)
    # None means use immutable imported text; an empty string is a real edit.
    text_override: str | None = None
    human_edited: bool = False
    # Immutable ancestor IDs for split/merge provenance; id is the sole live
    # sentence identity. Editing text/times or reordering never changes it.
    lineage_ids: list[str] = field(default_factory=list)
    # Main editable text is not necessarily a translation. Imported evidence
    # stays immutable; editing a source lyric never silently makes it Chinese.
    text_role: str = 'translation'
    speaker_id: str = ''
    speaker_style: dict = field(default_factory=dict)
    # Actor and named Style are independent. Legacy speaker_style is a detached
    # appearance snapshot; when style_id is set the profile is authoritative.
    style_id: str = ''
    style_overrides: dict = field(default_factory=dict)

    alignment_tokens: list[dict] = field(default_factory=list)
    speaker_name: str = ''

    def __post_init__(self):
        from .alignment_tokens import validate_alignment_tokens
        validate_alignment_tokens(self.alignment_tokens)

    def __getattribute__(self, name):
        if name not in ('__dict__', '_graph_binding', '__class__'):
            binding = object.__getattribute__(self, '__dict__').get('_graph_binding')
            if binding and name in _MAPPING_FIELD_NAMES:
                return binding[0].value(binding[1], name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        binding = self.__dict__.get('_graph_binding')
        if binding and name in _MAPPING_FIELD_NAMES:
            binding[0].set_value(binding[1], name, value)
        else: object.__setattr__(self, name, value)

    def __copy__(self):
        return Mapping(**asdict(self))

    def __deepcopy__(self, memo):
        result = Mapping(**deepcopy(asdict(self), memo))
        memo[id(self)] = result
        return result

    @property
    def sentence_id(self):
        return self.id


_MAPPING_FIELD_NAMES = frozenset(f.name for f in fields(Mapping))


@dataclass
class Project:
    version: int = 2
    inputs: dict = field(default_factory=dict)
    settings: dict = field(default_factory=lambda: {
        "engine": "rapidocr", "language": "jpn+eng", "sample_fps": 3.0,
        "roi": None, "txt_groups": False, "visibility": 0.62,
        "max_gap": 0.45, "api_enabled": False, "api_model": "deepseek-flash",
        "api_max_calls": 50, "api_retries": 3,
    })
    video: dict = field(default_factory=dict)
    lyrics: list[Lyric] = field(default_factory=list)
    translations: list[Translation] = field(default_factory=list)
    visuals: list[Visual] = field(default_factory=list)
    mappings: list[Mapping] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    api_records: list[dict] = field(default_factory=list)
    stage: str = "new"

    timebase: dict = field(default_factory=lambda: Timebase().to_dict())

    alignment_graph: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        from .alignment_graph import AlignmentGraph
        rows = object.__getattribute__(self, '__dict__').pop('mappings', [])
        raw = object.__getattribute__(self, 'alignment_graph')
        graph = AlignmentGraph.from_dict(raw) if isinstance(raw, dict) else AlignmentGraph(rows)
        object.__setattr__(self, 'alignment_graph', graph)

    def __getattribute__(self, name):
        if name == 'mappings':
            graph = object.__getattribute__(self, '__dict__').get('alignment_graph')
            if graph is not None and not isinstance(graph, dict): return graph.timeline
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name == 'mappings':
            graph = self.__dict__.get('alignment_graph')
            if graph is not None and not isinstance(graph, dict):
                graph.replace(value); return
        object.__setattr__(self, name, value)

    def replace_timeline(self, rows):
        self.alignment_graph.replace(rows)

    def project_timeline(self, *, detached=True):
        return self.alignment_graph.project(detached=detached)

    def __copy__(self):
        return Project.from_dict(self.to_dict())

    def __deepcopy__(self, memo):
        result = Project.from_dict(deepcopy(self.to_dict(), memo))
        memo[id(self)] = result
        return result

    @property
    def main_timeline(self):
        """Live cue projection of the authoritative alignment graph."""
        return self.mappings

    def validate_timeline(self):
        """Validate the shared editing domain without requiring media or OCR."""
        Timebase.from_dict(self.timebase)
        from .speakers import validate_speakers
        validate_speakers(self)
        from .styles import validate_project_styles
        validate_project_styles(self)
        ids=[row.id for row in self.main_timeline]
        if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('时间轴字幕编号不能为空或重复。')
        for row in self.main_timeline:
            validate_boundaries(row.start,row.end)
            if row.text_role not in TEXT_ROLES:
                raise ValueError('主时间轴文本类型无效。')
            if row.text_role != 'translation' and not isinstance(row.text_override, str):
                raise ValueError('演唱原文缺少可编辑正文。')
            if (not isinstance(row.lineage_ids,list) or
                    any(not isinstance(i,str) or not i for i in row.lineage_ids) or
                    len(set(row.lineage_ids))!=len(row.lineage_ids) or row.id in row.lineage_ids):
                raise ValueError('字幕拆分/合并来源记录无效。')
        return self

    def to_dict(self):
        out = {f.name: deepcopy(getattr(self, f.name)) for f in fields(self)
               if f.name not in ('alignment_graph', 'mappings', 'lyrics', 'translations', 'visuals')}
        for name in ('lyrics', 'translations', 'visuals'):
            out[name] = [asdict(row) for row in getattr(self, name)]
        self.alignment_graph.sync_unbound(self.lyrics, self.translations, self.visuals)
        out['alignment_graph'] = self.alignment_graph.to_dict()
        out['mappings'] = [asdict(row) for row in self.project_timeline(detached=False)]
        return out

    @classmethod
    def from_dict(cls, obj):
        if not isinstance(obj, dict):
            raise ValueError('工程文件格式错误。')
        if obj.get("version") != 2:
            raise ValueError('不支持该版本的工程文件。')
        obj = deepcopy(obj)
        # Optional v0.22 evidence: absent in v3/v0.21 projects, never a required migration.
        for row in obj.get('mappings',[]):
            if isinstance(row,dict):row.setdefault('alignment_tokens',[])
        for name in ('lyrics','translations','visuals','mappings'):
            if name in obj and (not isinstance(obj[name],list) or any(not isinstance(row,dict) for row in obj[name])):
                raise ValueError('Invalid project entity table: '+name)
        graph_value = obj.get('alignment_graph')
        from .speakers import migrate_names
        migrate_names(obj, graph_value)
        if graph_value is not None:
            from .alignment_graph import AlignmentGraph
            graph = AlignmentGraph.from_dict(graph_value)
            mirror = [asdict(row) for row in graph.project(detached=False)]
            if 'mappings' in obj and obj['mappings'] != mirror:
                raise ValueError('Alignment graph and compatibility timeline disagree')
            obj['mappings'] = mirror
            namespaces = {'lyric_ids': {r.get('id') for r in obj.get('lyrics',[])},
                          'visual_ids': {r.get('id') for r in obj.get('visuals',[])},
                          'translation_ids': {r.get('id') for r in obj.get('translations',[])}}
            for row in mirror + graph.unbound_edges:
                if any(not set(row[name]) <= identities for name,identities in namespaces.items()):
                    raise ValueError('Alignment graph references a missing evidence node')
        Timebase.from_dict(obj.get("timebase", Timebase().to_dict()))
        for key, kind in (("lyrics", Lyric), ("translations", Translation),
                          ("visuals", Visual), ("mappings", Mapping)):
            obj[key] = [kind(**row) for row in obj.get(key, [])]
        return cls(**obj)

    def unresolved(self):
        return [m for m in self.mappings if not m.ignored and
                (not valid_time(m.start, m.end) or not m.confirmed)]
