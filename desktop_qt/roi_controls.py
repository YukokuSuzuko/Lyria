"""Edit project OCR regions as one undoable action, including optional attribute sync."""
from copy import deepcopy
from uuid import uuid4

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout
from PySide6.QtCore import Qt

from lyricsync.multi_roi import bounding_box, project_regions, validate_regions, MAX_REGIONS
from lyricsync.roi_attribution import KEY, sync
from lyricsync.styles import project_profiles
from lyricsync.speakers import region_name, valid_name


def region_choices(candidates, *, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle('选择探测到的歌词带')
    layout = QVBoxLayout(dialog)
    listing = QListWidget()
    listing.setObjectName('regionSuggestionChoices')
    layout.addWidget(listing)
    for band in candidates:
        examples = ' / '.join(band.get('examples', [])[:3])
        note = ' · 疑似固定角标' if band.get('watermark') else ' · 疑似固定数字' if band.get('static_number') else ''
        item = QListWidgetItem(f"{band['hits']} 次命中 · {examples or '无文本样例'}{note}", listing)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        choose = not (band.get('watermark') or band.get('static_number')) and listing.count() <= MAX_REGIONS
        item.setCheckState(Qt.CheckState.Checked if choose else Qt.CheckState.Unchecked)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(buttons)
    def selected():
        return [c for i,c in enumerate(candidates) if listing.item(i).checkState() == Qt.CheckState.Checked]
    def validate(*_):
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(1 <= len(selected()) <= MAX_REGIONS)
    listing.itemChanged.connect(validate)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    validate()
    return selected() if dialog.exec() == QDialog.DialogCode.Accepted else []


def store(bridge, regions, *, enabled=True):
    project = bridge.document.project
    width, height = project.video['width'], project.video['height']
    if regions and any(r.get('enabled', True) for r in regions):
        regions = validate_regions(regions, width, height)
    elif regions:
        validate_regions([dict(r,enabled=True) for r in regions], width, height)
    original = project_regions(project.settings, width, height)
    styles = project_profiles(project)
    for region in regions:
        if region.get('speaker_id') and not region_name(project, region):
            raise ValueError('旧选区引用了不存在的说话人。')
        if 'speaker_name' in region or region.get('speaker_id'):
            region['speaker_name'] = valid_name(region_name(project, region))
            region.pop('speaker_id', None)
        if region.get('style_id') and region['style_id'] not in styles:
            raise ValueError('所选样式已不存在。')
    before = {r['id']: r for r in original}
    changed = {r['id']: {field for field in ('speaker_name', 'style_id')
                         if r.get(field, '') != (region_name(project, before[r['id']]) if field == 'speaker_name' else before[r['id']].get(field, ''))}
               for r in regions if r['id'] in before}
    changed = {identity: fields for identity, fields in changed.items() if fields}
    update = False
    doc_id, revision = bridge.document.id, bridge.document.revision
    if changed and any(changed.keys() & set(v.get('regions', [])) for v in project.settings.get(KEY, {}).values()):
        box = QMessageBox()
        box.setWindowTitle('选区归属已改变')
        box.setText('是否也更新由该选区生成、归属仍未手改的现有句子？')
        future = box.addButton('仅下次识别生效', QMessageBox.ButtonRole.AcceptRole)
        update_button = box.addButton('同步未手改归属', QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(future)
        box.exec()
        if box.clickedButton() not in (future,update_button):
            return False
        update = box.clickedButton() is update_button
    if (bridge.document.id, bridge.document.revision) != (doc_id, revision):
        raise ValueError('修改选区期间工程已变动，请重新操作。')
    if regions == original and bool(project.settings.get('ocr_multi_region')) == bool(regions and enabled):
        return True
    bridge.history.push(project, '调整 OCR 选区与归属')
    project.settings['ocr_regions'] = deepcopy(regions)
    project.settings['ocr_multi_region'] = bool(regions and enabled)
    if not regions:
        project.settings['roi'] = None
    elif enabled:
        active = [r for r in regions if r['enabled']]
        if active and (len(active) == 1 or not project.settings.get('roi')):
            project.settings['roi'] = bounding_box(active)
    if update:
        sync(project, regions, changed)
    bridge._changed()
    return True


def drawn_box(value, width, height):
    if (not isinstance(value, list) or len(value) != 4 or
            any(type(v) not in (int, float) or not 0 <= v <= 1 for v in value)):
        raise ValueError('画面坐标无效。')
    return [round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(value)]


def change(bridge, params):
    if getattr(bridge, '_candidate_task', {}).get('running') or getattr(bridge, '_task', {}).get('running'):
        raise ValueError('当前任务完成后可编辑选区。')
    project = bridge.document.project
    width, height = project.video.get('width',0), project.video.get('height',0)
    if not width or not height:
        raise ValueError('请先载入视频。')
    regions = project_regions(project.settings,width,height)
    action = params.get('action','edit')
    if action == 'edit':
        from .multi_roi_dialog import open_multi_roi
        return open_multi_roi(bridge)
    if action == 'clear':
        return store(bridge, [])
    if action == 'add':
        if len(regions) >= MAX_REGIONS:
            raise ValueError('最多可设置 4 个选区。')
        slots = [(0.10,0.70,0.90,0.93), (0.10,0.15,0.90,0.35),
                 (0.08,0.40,0.47,0.60), (0.53,0.40,0.92,0.60)]
        boxes = [[round(x*width),round(y*height),round(u*width),round(v*height)] for x,y,u,v in slots]
        band = (drawn_box(params['box'], width, height) if 'box' in params else
                next((box for box in boxes if all(r['box'] != box for r in regions)), boxes[0]))
        name = next(f'区域 {n}' for n in range(1, MAX_REGIONS+1) if all(r.get('name') != f'区域 {n}' for r in regions))
        regions.append(dict(id='roi-'+uuid4().hex[:12],name=name,box=band,enabled=True))
        return store(bridge,regions)
    selected = next((r for r in regions if r['id']==params.get('id')),None)
    if selected is None:
        raise ValueError('所选区域已改变。')
    if action == 'remove':
        regions.remove(selected)
    elif action == 'geometry':
        selected['box'] = drawn_box(params.get('box'), width, height)
    elif action == 'enabled' and type(params.get('value')) is bool:
        selected['enabled']=params['value']
    elif action == 'name':
        name = params.get('value')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('请填写选区名称。')
        selected['name'] = name.strip()
    elif action in ('speaker_name', 'speaker_id'):
        value = params.get('value', '')
        if action == 'speaker_id' and value:
            value = project.settings.get('speaker_profiles', {}).get(value, {}).get('name')
        selected['speaker_name'] = valid_name(value)
        selected.pop('speaker_id', None)
    elif action == 'style_id':
        selected['style_id'] = params.get('value', '')
    else:
        raise ValueError('未知选区操作。')
    return store(bridge, regions)
