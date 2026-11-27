"""fontvuer-inspired virtualized system font waterfall preview widget & standalone dialog."""
from __future__ import annotations

import json
from pathlib import Path
from PySide6.QtCore import (
    QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal, QTimer,
)
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListView, QMessageBox,
    QPushButton, QSlider, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget,
)
from lyricsync.storage import atomic_json
from .native_theme import apply_dialog_theme


DEFAULT_SAMPLES = {
    'zh': '月色微醺，旋律与歌词恰好相遇 123',
    'ja': '君の名を呼びながら夜空を駆ける 123',
    'en': 'Moonlit Lilac · Singing Lyrics 0123456',
}


def load_favorite_fonts(storage_dir):
    if not storage_dir:
        return []
    path = Path(storage_dir) / 'favorite-fonts.json'
    try:
        data = json.loads(path.read_text('utf-8'))
        items = data.get('favorites', []) if isinstance(data, dict) else []
        return [str(x) for x in items if isinstance(x, str) and x.strip()]
    except (OSError, ValueError, TypeError):
        return []


def save_favorite_fonts(storage_dir, favorites):
    if not storage_dir:
        return
    path = Path(storage_dir) / 'favorite-fonts.json'
    ordered = list(dict.fromkeys(str(x).strip() for x in favorites if str(x).strip()))
    atomic_json(path, {'version': 1, 'favorites': ordered})


_CACHED_FONT_INVENTORY = None


def warm_font_inventory():
    """Populate module-level font family cache so font dialogs open instantaneously."""
    return system_font_inventory()


def count_missing_glyphs(family: str, sample: str) -> int:
    """Count non-whitespace characters in `sample` unsupported by `family`."""
    if not sample or not sample.strip():
        return 0
    font = QFont(family)
    fm = QFontMetricsF(font)
    missing = 0
    seen = set()
    for ch in sample:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        if not fm.inFontUcs4(ord(ch)):
            missing += 1
    return missing


def system_font_inventory():
    """Scan installed system font families with script tags (cached across opens)."""
    global _CACHED_FONT_INVENTORY
    if _CACHED_FONT_INVENTORY is not None:
        return list(_CACHED_FONT_INVENTORY)
    zh_set = set(QFontDatabase.families(QFontDatabase.WritingSystem.SimplifiedChinese)) | set(
        QFontDatabase.families(QFontDatabase.WritingSystem.TraditionalChinese)
    )
    ja_set = set(QFontDatabase.families(QFontDatabase.WritingSystem.Japanese))
    families = QFontDatabase.families()
    if not families:
        families = ['Microsoft YaHei', 'SimHei', 'SimSun', 'Yu Gothic UI', 'Meiryo', 'Arial', 'Segoe UI']
        zh_set.update(['Microsoft YaHei', 'SimHei', 'SimSun'])
        ja_set.update(['Yu Gothic UI', 'Meiryo'])
    rows = []
    for family in families:
        rows.append({
            'family': family,
            'vertical_alias': family.startswith('@'),
            'zh': family in zh_set,
            'ja': family in ja_set,
        })
    _CACHED_FONT_INVENTORY = rows
    return list(rows)


class FontWaterfallModel(QAbstractListModel):
    FamilyRole = Qt.ItemDataRole.UserRole + 1
    FavoriteRole = Qt.ItemDataRole.UserRole + 2
    MissingRole = Qt.ItemDataRole.UserRole + 3
    TagsRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._sample = DEFAULT_SAMPLES['zh']
        self._favorites = set()
        self._missing_cache = {}
        self._warm_cursor = 0
        self._warm_timer = QTimer(self)
        self._warm_timer.setInterval(18)
        self._warm_timer.timeout.connect(self._warm_batch)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        row = self._items[index.row()]
        family = row['family']
        if role in (Qt.ItemDataRole.DisplayRole, self.FamilyRole):
            return family
        if role == self.FavoriteRole:
            return family in self._favorites
        if role == self.MissingRole:
            key = (family, self._sample)
            if key not in self._missing_cache:
                self._missing_cache[key] = count_missing_glyphs(family, self._sample)
            return self._missing_cache[key]
        if role == self.TagsRole:
            tags = []
            if row.get('zh'):
                tags.append('中')
            if row.get('ja'):
                tags.append('日')
            return '·'.join(tags) if tags else '西文'
        return None

    def set_items(self, items):
        self._warm_timer.stop()
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()
        self._warm_cursor = 0
        if self._items:
            self._warm_timer.start()

    def _warm_batch(self):
        if self._warm_cursor >= len(self._items):
            self._warm_timer.stop()
            return
        start = self._warm_cursor
        end = min(len(self._items), start + 18)
        for idx in range(start, end):
            family = self._items[idx]['family']
            key = (family, self._sample)
            if key not in self._missing_cache:
                self._missing_cache[key] = count_missing_glyphs(family, self._sample)
        self._warm_cursor = end
        self.dataChanged.emit(self.index(start), self.index(end - 1), [self.MissingRole])

    def set_sample(self, text):
        self._sample = text
        self._missing_cache.clear()
        self._warm_cursor = 0
        if self._items:
            self.dataChanged.emit(self.index(0), self.index(len(self._items) - 1))
            self._warm_timer.start()

    def set_favorites(self, favs):
        self._favorites = set(favs)
        if self._items:
            self.dataChanged.emit(self.index(0), self.index(len(self._items) - 1), [self.FavoriteRole])

    def item_at(self, row):
        return self._items[row] if 0 <= row < len(self._items) else None


class FontCardDelegate(QStyledItemDelegate):
    def __init__(self, state_provider, parent=None):
        super().__init__(parent)
        self._state = state_provider

    def sizeHint(self, option, index):
        st = self._state()
        preview_px = max(18, min(72, int(st['preview_size'])))
        h = max(78, preview_px + 54)
        return QSize(max(240, option.rect.width() - 6), h)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        st = self._state()
        family = index.data(FontWaterfallModel.FamilyRole) or ''
        is_fav = bool(index.data(FontWaterfallModel.FavoriteRole))
        missing = int(index.data(FontWaterfallModel.MissingRole) or 0)
        tags = index.data(FontWaterfallModel.TagsRole) or ''
        sample = st['sample'] or DEFAULT_SAMPLES['zh']
        preview_px = max(18, min(72, int(st['preview_size'])))
        selected = family.casefold() == st['selected_font'].casefold() or bool(option.state & QStyle.StateFlag.State_Selected)

        card = option.rect.adjusted(4, 3, -4, -3)
        bg_mode = st['bg_mode']
        frame_img = st.get('frame_image')
        if bg_mode == 'frame' and frame_img is not None and not frame_img.isNull():
            path = QPainterPath()
            path.addRoundedRect(card, 10, 10)
            painter.setClipPath(path)
            painter.drawImage(card, frame_img)
            painter.fillRect(card, QColor(18, 14, 28, 128))
            painter.setClipping(False)
            meta_color = QColor('#E7DFF5')
            header_bg = QColor(24, 19, 36, 175)
        elif bg_mode == 'dark':
            painter.setBrush(QColor('#1E192B'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(card, 10, 10)
            meta_color = QColor('#C7BBE0')
            header_bg = QColor('#29223A')
        else:
            painter.setBrush(QColor('#FFFFFF'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(card, 10, 10)
            meta_color = QColor('#6C607B')
            header_bg = QColor('#F7F4FB')

        border_pen = QPen(QColor('#62408D') if selected else QColor('#D9D0E8'), 2.0 if selected else 1.0)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card, 10, 10)

        # Header strip inside card
        header_rect = QRect(card.left() + 1, card.top() + 1, card.width() - 2, 26)
        hdr_path = QPainterPath()
        hdr_path.addRoundedRect(header_rect, 9, 9)
        painter.fillPath(hdr_path, header_bg)

        meta_font = QFont(option.font)
        meta_font.setPixelSize(11)
        meta_font.setBold(selected)
        painter.setFont(meta_font)
        painter.setPen(QColor('#8B5CF6') if selected and bg_mode != 'light' else (QColor('#62408D') if selected else meta_color))
        star = '★' if is_fav else '☆'
        title = f"{star}  {family}  ({tags})"
        right_reserved = 108 if missing > 0 else 48
        painter.drawText(
            header_rect.adjusted(10, 0, -right_reserved, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, max(60, header_rect.width() - right_reserved - 10)),
        )

        if missing > 0:
            warn_font = QFont(option.font)
            warn_font.setPixelSize(10)
            painter.setFont(warn_font)
            painter.setPen(QColor('#F59E0B' if bg_mode != 'light' else '#B45309'))
            painter.drawText(header_rect.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"⚠ 缺 {missing} 字")
        elif selected:
            sel_font = QFont(option.font)
            sel_font.setPixelSize(10)
            painter.setFont(sel_font)
            painter.setPen(QColor('#A78BFA' if bg_mode != 'light' else '#62408D'))
            painter.drawText(header_rect.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "当前选中")

        # Preview subtitle text line
        preview_rect = QRect(card.left() + 12, header_rect.bottom() + 4, card.width() - 24, card.bottom() - header_rect.bottom() - 8)
        pfont = QFont(family)
        pfont.setPixelSize(preview_px)
        pfont.setBold(bool(st['bold']))
        pfont.setItalic(bool(st['italic']))
        painter.setFont(pfont)

        color = QColor(st['color'])
        if not color.isValid():
            color = QColor('#FFFFFF')
        if bg_mode == 'light' and color.lightness() > 215:
            # Draw subtle outline on paper background so white ASS colors stay readable
            painter.setPen(QColor('#4D415E'))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(preview_rect.translated(dx, dy), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sample)
        elif bg_mode != 'light':
            painter.setPen(QColor(16, 12, 24, 210))
            painter.drawText(preview_rect.translated(1, 1), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sample)
        painter.setPen(color)
        painter.drawText(preview_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sample)

        painter.restore()


class FontPreviewWidget(QWidget):
    """Reusable fontvuer waterfall widget: search, CJK filter, favorites, missing-glyph guard."""

    fontSelected = Signal(str)
    appearanceChanged = Signal()

    def __init__(
        self,
        parent=None,
        *,
        initial_font='Microsoft YaHei',
        initial_sample='',
        initial_size=34,
        initial_color='#FFFFFF',
        initial_bold=False,
        initial_italic=False,
        storage_dir=None,
        frame_image=None,
        show_appearance_controls=True,
    ):
        super().__init__(parent)
        self.setObjectName('fontPreviewWidget')
        self._storage_dir = storage_dir
        self._favorites = set(load_favorite_fonts(storage_dir))
        self._inventory = system_font_inventory()
        self._selected_font = initial_font or 'Microsoft YaHei'
        self._color = initial_color or '#FFFFFF'
        self._bold = bool(initial_bold)
        self._italic = bool(initial_italic)
        self._preview_size = max(18, min(32, int(initial_size or 28)))
        self._bg_mode = 'dark'
        self._frame_image = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # Row 1: Sample lyric input + quick switches
        row_sample = QHBoxLayout()
        row_sample.setSpacing(6)
        self.sample_edit = QLineEdit(initial_sample or DEFAULT_SAMPLES['zh'])
        self.sample_edit.setObjectName('fontPreviewSampleEdit')
        self.sample_edit.setPlaceholderText('输入要试看的歌词或字幕文本…')
        row_sample.addWidget(QLabel('试看文本'))
        row_sample.addWidget(self.sample_edit, 1)
        for label, key in (('中文', 'zh'), ('日文', 'ja'), ('英文', 'en')):
            b = QPushButton(label)
            b.setStyleSheet('padding: 4px 10px; min-width: 48px;')
            b.clicked.connect(lambda _=False, k=key: self.sample_edit.setText(DEFAULT_SAMPLES[k]))
            row_sample.addWidget(b)
        root.addLayout(row_sample)

        # Row 2: Search + script filter + favorite toggle + count
        row_filter = QHBoxLayout()
        row_filter.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('fontPreviewSearchEdit')
        self.search_edit.setPlaceholderText('搜索已安装字体名称（如 思源 / 霞鹜 / Noto / Yu）…')
        self.script_combo = QComboBox()
        self.script_combo.setObjectName('fontPreviewScriptCombo')
        self.script_combo.addItem('全部字体', 'all')
        self.script_combo.addItem('支持中文', 'zh')
        self.script_combo.addItem('支持日文', 'ja')
        self.only_favs = QCheckBox('★ 仅看收藏')
        self.only_favs.setObjectName('fontPreviewOnlyFavs')
        self.include_vertical = QCheckBox('含 @ 竖排别名')
        self.count_label = QLabel('')
        row_filter.addWidget(self.search_edit, 1)
        row_filter.addWidget(self.script_combo)
        row_filter.addWidget(self.only_favs)
        row_filter.addWidget(self.include_vertical)
        row_filter.addWidget(self.count_label)
        root.addLayout(row_filter)

        # Row 3: Live preview appearance bar
        row_look = QHBoxLayout()
        row_look.setSpacing(8)
        row_look.addWidget(QLabel('预览字号'))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(18, 72)
        self.size_slider.setValue(self._preview_size)
        self.size_label = QLabel(f'{self._preview_size} px')
        self.size_label.setMinimumWidth(42)
        self.bold_btn = QPushButton('B 粗体')
        self.bold_btn.setCheckable(True)
        self.bold_btn.setChecked(self._bold)
        self.italic_btn = QPushButton('I 斜体')
        self.italic_btn.setCheckable(True)
        self.italic_btn.setChecked(self._italic)
        self.color_btn = QPushButton('选色')
        self.color_btn.setObjectName('fontPreviewColorBtn')
        self.fav_current_btn = QPushButton('☆ 收藏当前字体')
        self.fav_current_btn.setObjectName('fontPreviewFavBtn')
        self.bg_combo = QComboBox()
        self.bg_combo.addItem('深色字幕底板', 'dark')
        self.bg_combo.addItem('浅色纸面底板', 'light')
        self.bg_combo.setCurrentIndex(max(0, self.bg_combo.findData(self._bg_mode)))
        row_look.addWidget(self.size_slider, 1)
        row_look.addWidget(self.size_label)
        if show_appearance_controls:
            row_look.addWidget(self.bold_btn)
            row_look.addWidget(self.italic_btn)
            row_look.addWidget(self.color_btn)
        row_look.addWidget(self.bg_combo)
        row_look.addWidget(self.fav_current_btn)
        root.addLayout(row_look)

        # Virtualized waterfall view
        self.model = FontWaterfallModel(self)
        self.model.set_favorites(self._favorites)
        self.view = QListView()
        self.view.setObjectName('fontWaterfallList')
        self.view.setModel(self.model)
        self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.delegate = FontCardDelegate(self._visual_state, self.view)
        self.view.setItemDelegate(self.delegate)
        root.addWidget(self.view, 1)

        # Connections
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(80)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search_edit.textChanged.connect(lambda _: self._search_timer.start())
        self.script_combo.currentIndexChanged.connect(self._apply_filter)
        self.only_favs.toggled.connect(self._apply_filter)
        self.include_vertical.toggled.connect(self._apply_filter)
        self.sample_edit.textChanged.connect(self._on_sample_changed)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        self.bold_btn.toggled.connect(self._on_bold_toggled)
        self.italic_btn.toggled.connect(self._on_italic_toggled)
        self.color_btn.clicked.connect(self._pick_color)
        self.bg_combo.currentIndexChanged.connect(self._on_bg_changed)
        self.fav_current_btn.clicked.connect(self.toggle_selected_favorite)
        self.view.clicked.connect(self._on_card_clicked)
        self.view.doubleClicked.connect(lambda idx: self.toggle_favorite_at(idx))

        self._update_color_button()
        self._apply_filter()
        self._sync_fav_button()

    def _visual_state(self):
        return {
            'sample': self.sample_edit.text(),
            'preview_size': self._preview_size,
            'bold': self._bold,
            'italic': self._italic,
            'color': self._color,
            'bg_mode': self._bg_mode,
            'frame_image': self._frame_image,
            'selected_font': self._selected_font,
        }

    def selected_font(self):
        return self._selected_font

    def preview_appearance(self):
        return {
            'font': self._selected_font,
            'size': self._preview_size,
            'color': self._color,
            'bold': self._bold,
            'italic': self._italic,
        }

    def set_appearance(self, *, font=None, color=None, bold=None, italic=None):
        if font is not None and font != self._selected_font:
            self._selected_font = font
        if color is not None and QColor(color).isValid():
            self._color = QColor(color).name().upper()
            self._update_color_button()
        if bold is not None and bool(bold) != self._bold:
            self._bold = bool(bold)
            self.bold_btn.blockSignals(True)
            self.bold_btn.setChecked(self._bold)
            self.bold_btn.blockSignals(False)
        if italic is not None and bool(italic) != self._italic:
            self._italic = bool(italic)
            self.italic_btn.blockSignals(True)
            self.italic_btn.setChecked(self._italic)
            self.italic_btn.blockSignals(False)
        self._sync_fav_button()
        self.view.viewport().update()

    def _update_color_button(self):
        self.color_btn.setText(self._color)
        c = QColor(self._color)
        fg = '#181422' if c.lightness() > 145 else '#FFFFFF'
        self.color_btn.setStyleSheet(f'QPushButton {{ background: {self._color}; color: {fg}; border: 1px solid #9A8AB4; border-radius: 6px; padding: 3px 10px; }}')

    def _sync_fav_button(self):
        is_fav = self._selected_font in self._favorites
        self.fav_current_btn.setText('★ 已收藏' if is_fav else '☆ 收藏当前')

    def _apply_filter(self):
        query = self.search_edit.text().strip().casefold()
        script = self.script_combo.currentData()
        only_fav = self.only_favs.isChecked()
        show_v = self.include_vertical.isChecked()
        filtered = []
        for row in self._inventory:
            if not show_v and row['vertical_alias']:
                continue
            if script == 'zh' and not row['zh']:
                continue
            if script == 'ja' and not row['ja']:
                continue
            if only_fav and row['family'] not in self._favorites:
                continue
            if query and query not in row['family'].casefold():
                continue
            filtered.append(row)
        # Put favorites first while keeping alphabetical order inside each bucket.
        filtered.sort(key=lambda r: (0 if r['family'] in self._favorites else 1, r['family'].casefold()))
        self.model.set_items(filtered)
        self.count_label.setText(f'共 {len(filtered)} 款')

    def _on_sample_changed(self, text):
        self.model.set_sample(text)

    def _on_size_changed(self, value):
        self._preview_size = int(value)
        self.size_label.setText(f'{self._preview_size} px')
        self.view.doItemsLayout()
        self.view.viewport().update()

    def _on_bold_toggled(self, checked):
        self._bold = bool(checked)
        self.view.viewport().update()
        self.appearanceChanged.emit()

    def _on_italic_toggled(self, checked):
        self._italic = bool(checked)
        self.view.viewport().update()
        self.appearanceChanged.emit()

    def _pick_color(self):
        chosen = QColorDialog.getColor(QColor(self._color), self, '选择字幕主颜色')
        if chosen.isValid():
            self._color = chosen.name().upper()
            self._update_color_button()
            self.view.viewport().update()
            self.appearanceChanged.emit()

    def _on_bg_changed(self):
        self._bg_mode = self.bg_combo.currentData() or 'dark'
        self.view.viewport().update()

    def _on_card_clicked(self, index):
        family = index.data(FontWaterfallModel.FamilyRole)
        if not family:
            return
        self._selected_font = family
        self._sync_fav_button()
        self.view.viewport().update()
        self.fontSelected.emit(family)

    def toggle_favorite_at(self, index):
        family = index.data(FontWaterfallModel.FamilyRole)
        if family:
            self._toggle_favorite_name(family)

    def toggle_selected_favorite(self):
        if self._selected_font:
            self._toggle_favorite_name(self._selected_font)

    def _toggle_favorite_name(self, family):
        if family in self._favorites:
            self._favorites.discard(family)
        else:
            self._favorites.add(family)
        save_favorite_fonts(self._storage_dir, self._favorites)
        self.model.set_favorites(self._favorites)
        self._sync_fav_button()
        if self.only_favs.isChecked():
            self._apply_filter()


def current_video_preview_qimage(bridge):
    """Best-effort snapshot of current paused/active video frame for font background preview."""
    try:
        video_path = bridge.document.project.inputs.get('video')
        if not video_path or not Path(video_path).is_file():
            return None
        stamp = float(bridge.media_controller.snapshot().get('positionSeconds', 0.0) or 0.0)
        from lyricsync.vision import Video
        _, frame = Video(video_path).at(max(0.0, stamp))
        if frame is None:
            return None
        h, w = frame.shape[:2]
        rgb = frame[:, :, ::-1].copy()
        return QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    except Exception:
        return None


def show_font_preview_dialog(bridge, parent=None):
    """Standalone Font Preview Workbench launched from Subtitle/Tools menu."""
    from lyricsync import styles
    from .review_actions import selected_ids
    project = bridge.document.project
    profiles = styles.project_profiles(project)
    current_id = styles.current_style_id(project)
    active_style = profiles[current_id]['style']

    sample_text = ''
    try:
        from lyricsync.editing import mapping_text
        row = bridge._row(bridge.selected) if bridge.selected else None
        if row is not None:
            sample_text = mapping_text(project, row).replace('\n', ' ')
    except Exception:
        sample_text = ''

    from . import carbon_widgets as c
    dialog = QDialog(parent)
    dialog.setObjectName('fontPreviewDialog')
    dialog.setWindowTitle('字体预览器')
    dialog.resize(940, 650)
    root = c.layout(dialog)
    body = QWidget(); body.setObjectName('carbonBody')
    layout = c.layout(body, margins=(18, 14, 18, 14), space=10)
    root.addWidget(body, 1)
    tip_card = c.card(layout); tb = c.band(tip_card, (14, 9, 14, 9))
    tip = QLabel('已开启缺字检测与收藏同步：标注「⚠ 缺字」代表当前样本字在该字体下将显示方块；双击卡片或点星标可收藏。')
    tip.setWordWrap(True)
    tb.addWidget(tip, 1)

    widget = FontPreviewWidget(
        dialog,
        initial_font=active_style.get('font', 'Microsoft YaHei'),
        initial_sample=sample_text or DEFAULT_SAMPLES['zh'],
        initial_size=min(48, max(24, int(active_style.get('size', 36)))),
        initial_color=active_style.get('color', '#FFFFFF'),
        initial_bold=active_style.get('bold', False),
        initial_italic=active_style.get('italic', False),
        storage_dir=bridge.storage_dir,
    )
    layout.addWidget(widget, 1)

    footer = c.footer(root)
    chosen_label = QLabel(f"已选字体：{widget.selected_font()}")
    chosen_label.setObjectName('fontPreviewChosenLabel')
    widget.fontSelected.connect(lambda name: chosen_label.setText(f'已选字体：{name}'))
    footer.addWidget(chosen_label, 1)

    copy_btn = QPushButton('复制字体名')
    copy_btn.clicked.connect(lambda: (QApplication.clipboard().setText(widget.selected_font()), bridge._message(f'已复制字体名：{widget.selected_font()}')))
    footer.addWidget(copy_btn)

    apply_btn = QPushButton(f"应用字体到「{profiles[current_id]['name']}」")
    apply_btn.setObjectName('applyPreviewFontButton')
    apply_btn.setProperty('styleRole', 'primary')
    apply_btn.setEnabled(not bridge._state.get('candidateReadOnly', False))

    def apply_to_style():
        from copy import deepcopy
        updated = deepcopy(profiles)
        updated[current_id]['style']['font'] = widget.selected_font()
        updated[current_id]['style']['color'] = widget.preview_appearance()['color']
        updated[current_id]['style']['bold'] = widget.preview_appearance()['bold']
        updated[current_id]['style']['italic'] = widget.preview_appearance()['italic']
        styles.save_project_profiles(bridge.document.project, bridge.history, updated, current_id)
        bridge._changed()
        bridge._message(f"已将「{widget.selected_font()}」应用到工程样式「{profiles[current_id]['name']}」。")
        dialog.accept()

    apply_btn.clicked.connect(apply_to_style)
    footer.addWidget(apply_btn)

    close_btn = QPushButton('关闭')
    close_btn.clicked.connect(dialog.reject)
    footer.addWidget(close_btn)

    c.theme(dialog)
    return dialog.exec()
