"""Private, exact-input OCR reuse. Never reuse approximate image similarity."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
import hashlib
import time
from .storage import Cache,file_hash
from .vision import Detection


class CachedOCR:
    def __init__(self,provider,cache,video_digest,roi,settings,cancel=lambda:False):
        self.provider=provider;self.cache=cache;self.signature=deepcopy(provider.signature)
        self.cancel=cancel;self.memory=OrderedDict()
        # Hash the implementation as well as the actual model/runtime signature.
        # This deliberately invalidates reuse after OCR/preprocessing code edits.
        import cv2,numpy,marshal
        from .vision import RapidOCRProvider,TesseractOCR,ocr_similarity,normalized,box_overlap
        from .ocr_preprocess import subtitle_image,needs_raw_recovery,recover_from_raw,text_key
        from .ocr_spatial import spatial_lines, tagged, deduplicate, same_slot, duplicate_region, _split_multiline_box, channels, component, vertical_overlap
        implementation=hashlib.sha256(marshal.dumps(tuple(f.__code__ for f in (RapidOCRProvider.__init__,RapidOCRProvider.recognize,RapidOCRProvider._recognize_input,RapidOCRProvider._recognize_spatial_line,subtitle_image,needs_raw_recovery,recover_from_raw,text_key,TesseractOCR.__init__,TesseractOCR.recognize,ocr_similarity,normalized,box_overlap,spatial_lines,tagged,deduplicate,same_slot,duplicate_region,_split_multiline_box,channels,component,vertical_overlap)))).hexdigest()
        from importlib.metadata import version,PackageNotFoundError
        libraries={}
        for name in ('rapidocr','onnxruntime','onnxruntime-directml','pillow'):
            try:libraries[name]=version(name)
            except PackageNotFoundError:libraries[name]=None
        context={'version':2,'libraries':libraries,'video':video_digest,'roi':list(roi),'ocr':self.signature,
                 'implementation':implementation,
                 'opencv':cv2.__version__,'numpy':numpy.__version__,
                 'options':{key:settings.get(key) for key in ('engine','language','ocr_device','ocr_threads','ocr_model','ocr_preprocess','sample_fps','visibility','max_gap')}}
        executable=getattr(provider,'executable',None)
        if executable:context['executable']=file_hash(executable,cancel)
        self.scope=Cache.key('qc-ocr-scope',context)
        self.metrics={'requests':0,'model_calls':0,'memory_hits':0,'persistent_hits':0,'model_seconds':0.,'hash_seconds':0.,'cache_seconds':0.}

    def check(self):
        if self.cancel():raise InterruptedError('质检已取消，已识别的进度已缓存。')

    @staticmethod
    def valid(value):
        import math
        def number(n):return type(n) in (int,float) and math.isfinite(n)
        def box(v):return isinstance(v,list) and len(v)==4 and all(number(n) for n in v)
        def source(v):return isinstance(v,list) and len(v)<=8 and all(x in ('raw','minimum','tesseract') for x in v)
        def component(v, depth=0):
            required={'text','box','confidence','source_channels'}
            optional={'spatial_split','line_index','observation_status','eligible_for_alignment','provenance'}
            if not (isinstance(v,dict) and required<=set(v)<=required|optional
                    and isinstance(v['text'],str) and len(v['text'])<=100000 and box(v['box'])
                    and number(v['confidence']) and 0<=v['confidence']<=1 and source(v['source_channels'])):return False
            if 'line_index' in v and not (type(v['line_index']) is int and 0<=v['line_index']<=512):return False
            if 'eligible_for_alignment' in v and type(v['eligible_for_alignment']) is not bool:return False
            for key in ('spatial_split','observation_status'):
                if key in v and not (isinstance(v[key],str) and len(v[key])<=1000):return False
            if 'provenance' in v and not (depth==0 and isinstance(v['provenance'],list) and len(v['provenance'])<=512
                    and all(component(p,1) for p in v['provenance'])):return False
            return True
        def evidence(v):
            if not isinstance(v,dict):return False
            allowed={'source_channels','alternatives','observation_status','eligible_for_alignment',
                     'spatial_split','block_original','line_index','line_slot','spatial_version',
                     'components','assembly','component_text'}
            if not set(v)<=allowed:return False
            for key,item in v.items():
                if key=='source_channels' and not source(item):return False
                if key in ('alternatives','components') and not (isinstance(item,list) and len(item)<=512 and all(component(c) for c in item)):return False
                if key=='block_original' and not component(item):return False
                if key=='eligible_for_alignment' and type(item) is not bool:return False
                if key=='line_index' and not (type(item) is int and 0<=item<=512):return False
                if key=='line_slot' and not (isinstance(item,list) and len(item)==2 and all(number(x) for x in item) and item[1]>=item[0]):return False
                if key=='observation_status' and item not in ('normal','unresolved_multiline_block'):return False
                if key in ('spatial_split','spatial_version','assembly','component_text') and not (isinstance(item,str) and len(item)<=100000):return False
            return True
        return isinstance(value,list) and len(value)<=10000 and all(
            isinstance(d,dict) and {'text','box','confidence'}<=set(d)<={'text','box','confidence','evidence'}
            and isinstance(d['text'],str) and len(d['text'])<=100000 and box(d['box'])
            and number(d['confidence']) and 0<=d['confidence']<=1
            and evidence(d.get('evidence',{})) for d in value)

    def recognize(self,image):
        self.check();self.metrics['requests']+=1;t=time.perf_counter()
        key=Cache.key('qc-pixels-v1',{'scope':self.scope,'shape':list(image.shape),'dtype':str(image.dtype),'sha256':hashlib.sha256(image.tobytes()).hexdigest()})
        self.metrics['hash_seconds']+=time.perf_counter()-t;t=time.perf_counter()
        if key in self.memory:
            value=self.memory.pop(key);self.metrics['memory_hits']+=1
        else:
            value=self.cache.get(key)
            if self.valid(value):self.metrics['persistent_hits']+=1
            else:value=None
        self.metrics['cache_seconds']+=time.perf_counter()-t
        self.check()
        if value is None:
            t=time.perf_counter();result=self.provider.recognize(image)
            self.metrics['model_seconds']+=time.perf_counter()-t;self.metrics['model_calls']+=1
            self.check();value=[asdict(d) for d in result]
            if not self.valid(value):raise ValueError('OCR 识别返回数据异常。')
            t=time.perf_counter();self.cache.put(key,value);self.metrics['cache_seconds']+=time.perf_counter()-t
        self.memory[key]=value
        while len(self.memory)>128:self.memory.popitem(last=False)
        # scan offsets boxes in-place; cached observations must remain immutable.
        return [Detection(**deepcopy(d)) for d in value]
