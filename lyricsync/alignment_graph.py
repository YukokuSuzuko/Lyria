"""Authoritative alignment edges and cue specifications with live projections.

Mappings are adapters, never a second stored authority. No raw observation or
imported text is copied into the graph; links retain immutable occurrence IDs.
"""
from collections.abc import MutableSequence
from copy import deepcopy
from dataclasses import fields, asdict
import math

GRAPH_VERSION = 1
LINK_FIELDS = frozenset(('lyric_ids', 'visual_ids', 'translation_ids'))


class ProjectionReasons(list):
    """One-way write-through: cue edits update default edges, not vice versa."""
    def __init__(self, values=(), graph=None, identity=None):
        super().__init__(values);self.graph=graph;self.identity=identity
    def _change(self, operation, *args):
        before=list(self);result=operation(*args)
        if self.graph is not None:
            for table in (self.graph.visual_source_edges,self.graph.source_translation_edges):
                edge=table.get(self.identity)
                if edge is not None and edge['reasons']==before:edge['reasons']=list(self)
        return result
    def append(self,value):return self._change(super().append,value)
    def extend(self,values):return self._change(super().extend,list(values))
    def insert(self,index,value):return self._change(super().insert,index,value)
    def pop(self,index=-1):return self._change(super().pop,index)
    def remove(self,value):return self._change(super().remove,value)
    def clear(self):return self._change(super().clear)
    def reverse(self):return self._change(super().reverse)
    def sort(self,*,key=None,reverse=False):return self._change(lambda:super(ProjectionReasons,self).sort(key=key,reverse=reverse))
    def __setitem__(self,key,value):return self._change(super().__setitem__,key,value)
    def __delitem__(self,key):return self._change(super().__delitem__,key)
    def __iadd__(self,values):self.extend(values);return self
    def __imul__(self,count):self._change(super().__imul__,count);return self
    def __deepcopy__(self,memo):return deepcopy(list(self),memo)


class TimelineProjection(MutableSequence):
    def __init__(self, graph): self.graph = graph
    def __len__(self): return len(self.graph.cue_order)
    def __getitem__(self, index):
        ids = self.graph.cue_order[index]
        return [self.graph._views[i] for i in ids] if isinstance(index, slice) else self.graph._views[ids]
    def __setitem__(self, index, value):
        rows = list(self)
        rows[index] = value
        self.graph.replace(rows)
    def __delitem__(self, index):
        rows = list(self); del rows[index]; self.graph.replace(rows)
    def insert(self, index, value):
        rows = list(self); rows.insert(index, value); self.graph.replace(rows)
    def sort(self, *, key=None, reverse=False):
        self.graph.replace(sorted(self, key=key, reverse=reverse))
    def reverse(self): self.graph.replace(list(reversed(self)))
    def extend(self, values): self.graph.replace(list(self) + list(values))
    def clear(self): self.graph.replace([])
    def copy(self): return list(self)
    def __add__(self, other): return list(self) + list(other)
    def __radd__(self, other): return list(other) + list(self)
    def __eq__(self, other):
        try: return list(self) == list(other)
        except TypeError: return False
    def __repr__(self): return repr(list(self))
    def __deepcopy__(self, memo): return deepcopy(list(self), memo)


class AlignmentGraph:
    def __init__(self, rows=()):
        self.cue_order = []
        self.cue_specs = {}
        self.visual_source_edges = {}
        self.source_translation_edges = {}
        self._views = {}
        self.unbound_edges = []
        self.timeline = TimelineProjection(self)
        self.replace(rows)

    def value(self, identity, field):
        if field == 'visual_ids': return self.visual_source_edges[identity]['visual_ids']
        if field == 'lyric_ids': return self.visual_source_edges[identity]['lyric_ids']
        if field == 'translation_ids': return self.source_translation_edges[identity]['translation_ids']
        return self.cue_specs[identity][field]

    def set_value(self, identity, field, value):
        if field == 'id':
            if not isinstance(value, str) or not value: raise ValueError('Invalid cue identity')
            if value == identity: return
            if value in self.cue_specs: raise ValueError('Duplicate cue identity')
            for table in (self.cue_specs, self.visual_source_edges, self.source_translation_edges, self._views):
                table[value] = table.pop(identity)
            self.cue_specs[value]['id'] = value
            self.cue_order[self.cue_order.index(identity)] = value
            object.__setattr__(self._views[value], '_graph_binding', (self, value))
            reasons=self.cue_specs[value]['reasons']
            if isinstance(reasons,ProjectionReasons):reasons.identity=value
        elif field == 'visual_ids': self.visual_source_edges[identity]['visual_ids'] = value
        elif field == 'lyric_ids': self.visual_source_edges[identity]['lyric_ids'] = value
        elif field == 'translation_ids': self.source_translation_edges[identity]['translation_ids'] = value
        else:
            old_state=self._edge_state(identity)
            if field=='reasons':value=ProjectionReasons(value,self,identity)
            self.cue_specs[identity][field] = value
            if field in ('confidence', 'reasons'):
                for table in (self.visual_source_edges, self.source_translation_edges):
                    table[identity][field] = deepcopy(value)
                    if field=='confidence' and table[identity]['state']==old_state:
                        table[identity]['state']=self._edge_state(identity)
            if field in ('locked', 'confirmed', 'human_edited'):
                for table in (self.visual_source_edges, self.source_translation_edges):
                    table[identity]['state'] = self._edge_state(identity)

    def row_dict(self, identity):
        return {**deepcopy(self.cue_specs[identity]),
                'visual_ids': deepcopy(self.visual_source_edges[identity]['visual_ids']),
                'lyric_ids': deepcopy(self.visual_source_edges[identity]['lyric_ids']),
                'translation_ids': deepcopy(self.source_translation_edges[identity]['translation_ids'])}

    def replace(self, rows):
        from .model import Mapping
        rows = list(rows)
        if any(not isinstance(row, Mapping) for row in rows): raise ValueError('Timeline requires Mapping projections')
        values = [deepcopy(asdict(row)) for row in rows]
        identities = [row['id'] for row in values]
        if any(not isinstance(i, str) or not i for i in identities) or len(set(identities)) != len(identities):
            raise ValueError('Duplicate or invalid cue identity')
        specs = {}; vs = {}; st = {}; views = {}
        for row, value in zip(rows, values):
            identity = value['id']
            binding = row.__dict__.get('_graph_binding')
            if binding and binding[0] is not self: row = Mapping(**value)
            old_spec = self.cue_specs.get(identity, {})
            same_view = self._views.get(identity) is row
            specs[identity] = {k: old_spec[k] if same_view and k in old_spec and old_spec[k] == v else v
                               for k, v in value.items() if k not in LINK_FIELDS}
            for key in LINK_FIELDS:
                table = self.source_translation_edges if key == 'translation_ids' else self.visual_source_edges
                old_links = table.get(identity, {}).get(key)
                if same_view and old_links is not None and old_links == value[key]: value[key] = old_links
            old_spec = self.cue_specs.get(identity)
            state = ('manual_locked' if value['locked'] or value['confirmed'] or (value['human_edited'] and not value['method'].startswith('candidate_'))
                     else 'auto_high' if value['confidence'] >= .85 else 'uncertain')
            def metadata(table, link_names):
                old = table.get(identity)
                keep = old is not None and all(old.get(name) == value[name] for name in link_names)
                result = {key: deepcopy(old[key]) for key in ('state', 'confidence', 'reasons')} if keep else dict(state=state, confidence=value['confidence'], reasons=deepcopy(value['reasons']))
                if keep and old_spec:
                    for name in ('confidence','reasons'):
                        if value[name] != old_spec[name]: result[name] = deepcopy(value[name])
                    if any(value[name] != old_spec[name] for name in ('locked','confirmed','human_edited')): result['state']=state
                return result
            vs[identity] = dict(visual_ids=value['visual_ids'], lyric_ids=value['lyric_ids'], **metadata(self.visual_source_edges, ('visual_ids','lyric_ids')))
            st[identity] = dict(translation_ids=value['translation_ids'], **metadata(self.source_translation_edges, ('translation_ids',)))
            reasons=specs[identity]['reasons']
            if not isinstance(reasons,ProjectionReasons) or reasons.graph is not self:
                specs[identity]['reasons']=ProjectionReasons(reasons,self,identity)
            views[identity] = row
        # Validate and prepare all new records before detaching or adopting any view.
        removed = [(row, self.row_dict(i)) for i, row in self._views.items() if views.get(i) is not row]
        for row, values in removed:
            reasons=self.cue_specs[values['id']]['reasons']
            if isinstance(reasons,ProjectionReasons):reasons.graph=None
            object.__delattr__(row, '_graph_binding')
            for name, value in values.items(): object.__setattr__(row, name, value)
        self.cue_order, self.cue_specs = identities, specs
        self.visual_source_edges, self.source_translation_edges, self._views = vs, st, views
        for identity, row in views.items():
            # Primitive dataclass storage is cleared. All live reads use the graph.
            for descriptor in fields(Mapping): row.__dict__.pop(descriptor.name, None)
            object.__setattr__(row, '_graph_binding', (self, identity))

    def project(self, *, detached=True):
        if not detached: return self.timeline
        from .model import Mapping
        return [Mapping(**self.row_dict(i)) for i in self.cue_order]

    def manual_locked(self, identity):
        return any(table.get(identity, {}).get('state') == 'manual_locked' for table in (self.visual_source_edges, self.source_translation_edges))

    def _edge_state(self, identity):
        row = self.cue_specs[identity]
        return ('manual_locked' if row['locked'] or row['confirmed'] or (row['human_edited'] and not row['method'].startswith('candidate_'))
                else 'auto_high' if row['confidence'] >= .85 else 'uncertain')

    def sync_unbound(self, lyrics, translations, visuals):
        """Materialize explicit 0:N edges for entities with no cue association."""
        used_v={i for e in self.visual_source_edges.values() for i in e['visual_ids']}
        used_l={i for e in self.visual_source_edges.values() for i in e['lyric_ids']}
        used_t={i for e in self.source_translation_edges.values() for i in e['translation_ids']}
        available={'visual_ids':{v.id for v in visuals}-used_v,
                   'lyric_ids':{l.id for l in lyrics}-used_l,
                   'translation_ids':{t.id for t in translations}-used_t}
        retained=[]
        for edge in self.unbound_edges:
            if all(set(edge[k])<=available[k] for k in LINK_FIELDS):
                retained.append(edge)
                for key in LINK_FIELDS:available[key].difference_update(edge[key])
        defaults=([dict(id='unbound-visual:'+v.id, visual_ids=[v.id], lyric_ids=[], translation_ids=[], state='uncertain') for v in visuals if v.id in available['visual_ids']]
            + [dict(id='unbound-source:'+l.id, visual_ids=[], lyric_ids=[l.id], translation_ids=[], state='uncertain') for l in lyrics if l.id in available['lyric_ids']]
            + [dict(id='unbound-translation:'+t.id, visual_ids=[], lyric_ids=[], translation_ids=[t.id], state='uncertain') for t in translations if t.id in available['translation_ids']])
        self.unbound_edges = retained+defaults

    def to_dict(self):
        return dict(version=GRAPH_VERSION, cue_order=list(self.cue_order),
            cue_specs=[deepcopy(self.cue_specs[i]) for i in self.cue_order],
            visual_source_edges=[dict(id='vs:'+i, cue_id=i, **deepcopy(self.visual_source_edges[i])) for i in self.cue_order],
            source_translation_edges=[dict(id='st:'+i, cue_id=i, lyric_ids=list(self.visual_source_edges[i]['lyric_ids']),
                **deepcopy(self.source_translation_edges[i])) for i in self.cue_order],
            unbound_edges=deepcopy(self.unbound_edges))

    @classmethod
    def from_dict(cls, value):
        try: return cls._from_dict(value)
        except (TypeError, KeyError, AttributeError, OverflowError) as exc:
            raise ValueError('Malformed alignment graph') from exc

    @classmethod
    def _from_dict(cls, value):
        from .model import Mapping
        if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != GRAPH_VERSION:
            raise ValueError('Unsupported alignment graph version')
        value=deepcopy(value)
        for spec in value.get('cue_specs',[]):
            if isinstance(spec, dict):
                spec.setdefault('alignment_tokens', [])
                spec.setdefault('speaker_name', '')
        order = value.get('cue_order')
        if not isinstance(order, list) or any(not isinstance(i,str) or not i for i in order) or len(set(order)) != len(order): raise ValueError('Invalid graph cue order')
        def table(key, identity):
            rows = value.get(key)
            if not isinstance(rows, list): raise ValueError('Invalid graph table: '+key)
            result = {}
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get(identity),str) or not row.get(identity) or row.get(identity) in result: raise ValueError('Duplicate graph record')
                result[row.get(identity)] = row
            if set(result) != set(order): raise ValueError('Dangling graph record: '+key)
            return result
        specs=table('cue_specs','id'); vs=table('visual_source_edges','cue_id'); st=table('source_translation_edges','cue_id')
        rows=[]
        for identity in order:
            left,right=vs[identity],st[identity]
            if left.get('lyric_ids') != right.get('lyric_ids'): raise ValueError('Conflicting graph source edges')
            links={name: left.get(name) if name!='translation_ids' else right.get(name) for name in LINK_FIELDS}
            if any(not isinstance(ids,list) or any(not isinstance(i,str) or not i for i in ids) or len(ids)!=len(set(ids)) for ids in links.values()):
                raise ValueError('Invalid graph occurrence links')
            spec=specs[identity]
            if set(spec)!={f.name for f in fields(Mapping)}-LINK_FIELDS:raise ValueError('Invalid cue specification fields')
            confidence=spec.get('confidence')
            if type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1:raise ValueError('Invalid cue confidence')
            rows.append(Mapping(**deepcopy(spec), **deepcopy(links)))
        graph=cls(rows)
        for identity in order:
            for target, source in ((graph.visual_source_edges[identity],vs[identity]),(graph.source_translation_edges[identity],st[identity])):
                if source.get('state') not in ('manual_locked','auto_high','uncertain'): raise ValueError('Invalid graph edge state')
                if type(source.get('confidence')) not in (int,float) or not math.isfinite(source['confidence']) or not 0<=source['confidence']<=1 or not isinstance(source.get('reasons'),list) or any(not isinstance(r,str) for r in source['reasons']): raise ValueError('Invalid graph edge metadata')
                target.update({name:deepcopy(source[name]) for name in ('state','confidence','reasons')})

        unbound=value.get('unbound_edges',[])
        if not isinstance(unbound,list):raise ValueError('Invalid unbound graph edges')
        seen=set()
        occupied={name:{node for row in rows for node in getattr(row,name)} for name in LINK_FIELDS}
        for edge in unbound:
            if not isinstance(edge,dict) or set(edge)!={'id','visual_ids','lyric_ids','translation_ids','state'} or not isinstance(edge.get('id'),str) or not edge.get('id') or edge.get('id') in seen:raise ValueError('Invalid unbound graph edge')
            seen.add(edge['id'])
            if edge['state'] not in ('manual_locked','auto_high','uncertain') or any(not isinstance(edge[k],list) or any(not isinstance(i,str) or not i for i in edge[k]) or len(set(edge[k]))!=len(edge[k]) for k in LINK_FIELDS):raise ValueError('Invalid unbound node references')
            if not any(edge[name] for name in LINK_FIELDS):raise ValueError('Empty unbound relationship')
            for name in LINK_FIELDS:
                if occupied[name].intersection(edge[name]):raise ValueError('Conflicting bound/unbound or duplicate unbound node')
                occupied[name].update(edge[name])
        graph.unbound_edges=deepcopy(unbound)
        if graph.to_dict()!=value: raise ValueError('Alignment graph contains conflicting or unsupported fields')
        return graph
