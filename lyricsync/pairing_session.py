"""Dialog-local OCR pairing commands. Original evidence and text never mutate."""
from copy import deepcopy
from uuid import uuid4
from .ocr_correspondence import source_anchors
from .translation_pairing_draft import make_draft, output_pieces, available_slices, protected_usage, protected_row, row_spans
from .graphemes import validate_character_gap


class PairingSession:
    def __init__(self, project, frame_index=None):
        self.frame_index = frame_index
        self.project = deepcopy(project)
        self.anchors = list(source_anchors(project))
        self.texts = {t.id: t.text for t in project.translations
                      if not t.subtitle_metadata.get('capsule_derived')}
        _, used_visuals = protected_usage(project)
        self.reserved_anchors = {a.id for a in self.anchors if used_visuals.intersection(a.visual_ids)}
        self.available = available_slices(project)
        self.data = {'pieces': [self.piece([part]) for part in self.available], 'groups': [], 'ai': False}
        self.past, self.future = [], []
        self._seed_existing()
        self.initial_groups=deepcopy(self.data['groups'])
        self.initial_pool=self.project.translations

    def _seed_existing(self):
        # Reopen ordinary, complete-anchor pairings without silently changing
        # timed splits or user-edited rows. Unsupported old shapes stay intact.
        translations = {t.id: t for t in self.project.translations}
        groups = []; occupied = set()
        from .editing import mapping_text
        for row in self.project.mappings:
            if protected_row(self.project, row) or not row.translation_ids or not row.visual_ids:
                continue
            anchors = [a for a in self.anchors if set(a.visual_ids) <= set(row.visual_ids)]
            ids = [a.id for a in anchors]
            if not ids or occupied.intersection(ids):
                continue
            if row.start != min(a.start for a in anchors) or row.end != max(a.end for a in anchors):
                continue
            slices = [{'translation_id': i, 'start': a, 'end': b}
                      for i,a,b in row_spans(row, translations)]
            texts = [self.texts[x['translation_id']][x['start']:x['end']] for x in slices]
            current = mapping_text(self.project, row)
            if current == ''.join(texts):
                sizes = [len(slices)]
            elif current == '\n'.join(texts):
                sizes = [1] * len(slices)
            else:
                continue
            groups.append({'anchor_ids': ids, 'slices': slices, 'piece_sizes': sizes})
            occupied.update(ids)
        if groups:
            self.load_response({'groups': groups})
            self.data['ai'] = False
            self.past.clear(); self.future.clear()

    def piece(self, slices):
        return {'id': uuid4().hex, 'slices': deepcopy(slices)}

    def text(self, piece):
        return ''.join(self.texts[s['translation_id']][s['start']:s['end']] for s in piece['slices'])

    def _set_pool(self, project):
        self.project=project
        self.texts={t.id:t.text for t in project.translations if not t.subtitle_metadata.get('capsule_derived')}
        self.available=available_slices(project)

    def import_pool(self, incoming, *, replace=False):
        from .translation_pool import update_translation_pool
        used={i for g in self.data['groups'] for i in g['piece_ids']}
        reserved={s['translation_id'] for p in self.data['pieces'] if p['id'] in used for s in p['slices']}
        draft=update_translation_pool(self.project,incoming,replace=replace,reserved=reserved)
        if draft is self.project:return
        data=deepcopy(self.data)
        keep={t.id for t in draft.translations}
        data['pieces']=[p for p in data['pieces'] if all(s['translation_id'] in keep for s in p['slices'])]
        for row in draft.translations:
            if row.id not in self.texts and not row.subtitle_metadata.get('capsule_derived'):
                data['pieces'].append(self.piece([dict(translation_id=row.id,start=0,end=len(row.text))]))
        self.save(data)
        self._set_pool(draft)

    def save(self, data):
        self.past.append((self.project,deepcopy(self.data))); self.future.clear(); self.data = data

    def history(self, redo=False):
        src, dst = (self.future, self.past) if redo else (self.past, self.future)
        if src:
            dst.append((self.project,deepcopy(self.data)))
            project,self.data=src.pop()
            if project is not self.project:self._set_pool(project)

    def operate(self, action, ids, target=None, offset=None):
        d = deepcopy(self.data)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('请选择有效胶囊')
        pieces = {p['id']: p for p in d['pieces']}
        used = {i for g in d['groups'] for i in g['piece_ids']}
        if action == 'source':
            known = {a.id for a in self.anchors}
            occupied = {i for g in d['groups'] for i in g['anchor_ids']} | self.reserved_anchors
            if not set(ids) <= known or set(ids) & occupied:
                raise ValueError('锚点不存在或已使用')
            if target is None:
                d['groups'].append({'id': uuid4().hex, 'anchor_ids': ids[:], 'piece_ids': []})
            else:
                group = next((g for g in d['groups'] if g['id'] == target), None)
                if group is None: raise ValueError('配对槽不存在')
                group['anchor_ids'].extend(ids); group.pop('split_frames', None)
        elif action == 'merge_groups':
            chosen = [g for g in d['groups'] if g['id'] in ids]
            if len(chosen) < 2 or len(chosen) != len(ids): raise ValueError('请选择至少两个配对槽')
            group = {'id': uuid4().hex, 'anchor_ids': [i for g in chosen for i in g['anchor_ids']],
                     'piece_ids': [i for g in chosen for i in g['piece_ids']]}
            index = d['groups'].index(chosen[0])
            d['groups'] = [g for g in d['groups'] if g['id'] not in ids]
            d['groups'].insert(index, group)
        elif action == 'remove':
            if not set(ids) <= {g['id'] for g in d['groups']}:
                raise ValueError('配对槽已变化')
            d['groups'] = [g for g in d['groups'] if g['id'] not in ids]
        else:
            if not set(ids) <= pieces.keys() or set(ids) & used:
                raise ValueError('译文不存在或已使用；请先解除配对')
            ordered = [p for p in d['pieces'] if p['id'] in ids]
            if action == 'bind':
                group = next((g for g in d['groups'] if g['id'] == target), None)
                if group is None: raise ValueError('请选择配对槽')
                group['piece_ids'].extend(p['id'] for p in ordered)
                group.pop('split_frames', None)
            elif action == 'merge':
                if len(ordered) < 2: raise ValueError('至少选择两项')
                new = self.piece([s for p in ordered for s in p['slices']])
                index = d['pieces'].index(ordered[0])
                d['pieces'] = [p for p in d['pieces'] if p['id'] not in ids]
                d['pieces'].insert(index, new)
            elif action == 'split':
                if len(ordered) != 1: raise ValueError('每次切分一项')
                p = ordered[0]; text = self.text(p)
                validate_character_gap(text, offset)
                if offset in (0, len(text)): raise ValueError('请在胶囊内部切分')
                left, right, pos = [], [], 0
                for s in p['slices']:
                    n = s['end'] - s['start']; cut = max(0, min(n, offset-pos))
                    if cut: left.append(dict(s, end=s['start']+cut))
                    if cut < n: right.append(dict(s, start=s['start']+cut))
                    pos += n
                # Validate cuts in original strings, including merged fragment boundaries.
                for s in left+right:
                    validate_character_gap(self.texts[s['translation_id']], s['start'])
                    validate_character_gap(self.texts[s['translation_id']], s['end'])
                index = d['pieces'].index(p)
                d['pieces'][index:index+1] = [self.piece(left), self.piece(right)]
            else: raise ValueError('未知操作')
        d['ai'] = False
        self.save(d)

    def response(self):
        pieces = {p['id']: p for p in self.data['pieces']}
        if not self.data['groups']: raise ValueError('请先建立配对槽')
        if any(not g['piece_ids'] for g in self.data['groups']):
            raise ValueError('有配对槽未放入译文，请补全或移回两侧')
        return {'groups': [{'anchor_ids': g['anchor_ids'],
                            'slices': [s for i in g['piece_ids'] for s in pieces[i]['slices']],
                            'piece_sizes': [len(pieces[i]['slices']) for i in g['piece_ids']],
                            **({'split_frames': g['split_frames']} if 'split_frames' in g else {})}
                           for g in self.data['groups']]}

    def draft(self):
        return make_draft(self.project, self.response(), frame_index=self.frame_index)

    def load_response(self, response):
        clean = make_draft(self.project, response, frame_index=self.frame_index)
        for g in clean['groups']:
            if set(g['anchor_ids']) & self.reserved_anchors: raise ValueError('草案涉及已保护锚点')
            for part in g['slices']:
                if not any(a['translation_id']==part['translation_id'] and a['start']<=part['start']<part['end']<=a['end'] for a in self.available):
                    raise ValueError('草案重复使用已保护译文区间')
        d = {'pieces': [], 'groups': [], 'ai': True}
        used = {}
        for g in clean['groups']:
            capsules = [self.piece(parts) for parts in output_pieces(g)]; d['pieces'].extend(capsules)
            d['groups'].append({'id': uuid4().hex, 'anchor_ids': g['anchor_ids'], 'piece_ids': [p['id'] for p in capsules],
                                **({'split_frames': g['split_frames']} if 'split_frames' in g else {})})
            for s in g['slices']:
                used.setdefault(s['translation_id'], []).append((s['start'], s['end']))
        for available in self.available:
            i = available['translation_id']; cursor = available['start']; limit = available['end']
            spans = [(a,b) for a,b in used.get(i,[]) if cursor <= a < b <= limit]
            for start, end in sorted(spans) + [(limit,limit)]:
                if cursor < start:
                    d['pieces'].append(self.piece([{'translation_id': i, 'start': cursor, 'end': start}]))
                cursor = max(cursor,end)
        self.save(d)

    def set_split_frames(self, identity, frames):
        data = deepcopy(self.data)
        group = next((g for g in data['groups'] if g['id'] == identity), None)
        if group is None: raise ValueError('请选择配对槽')
        if frames is None: group.pop('split_frames', None)
        else: group['split_frames'] = frames
        old = self.data
        try:
            self.data = data
            self.draft()
        finally: self.data = old
        self.save(data)

    def sequential_fill(self):
        data = deepcopy(self.data)
        used_p = {i for g in data['groups'] for i in g['piece_ids']}
        pieces = [p['id'] for p in data['pieces'] if p['id'] not in used_p]
        empty = [g for g in data['groups'] if not g['piece_ids']]
        for group in empty:
            if not pieces: break
            group['piece_ids'].append(pieces.pop(0))
        used_a = {i for g in data['groups'] for i in g['anchor_ids']}
        anchors = [a.id for a in self.anchors if a.id not in used_a and a.id not in self.reserved_anchors]
        for i, piece in enumerate(pieces):
            data['groups'].append({'id': uuid4().hex, 'anchor_ids': anchors[i:i+1], 'piece_ids': [piece]})
        data['ai'] = False
        self.save(data)

    def untimed(self, ids):
        used = {i for g in self.data['groups'] for i in g['piece_ids']}
        known = {p['id'] for p in self.data['pieces']}
        if not ids or not set(ids) <= known or set(ids) & used: raise ValueError('请选择右侧未使用译文')
        data = deepcopy(self.data)
        data['groups'].append({'id': uuid4().hex, 'anchor_ids': [], 'piece_ids': ids[:]})
        self.save(data)
