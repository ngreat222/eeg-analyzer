"""预处理页：只处理连续数据（通道、滤波、坏导、重参考、ICA），不需要事件。

选择记录后显示处理概览，可进入对应的设置类别。
处理结果是“干净的连续数据”，事件和分段在「事件与分段」页做。
"""
import json
import os
import re
import unicodedata

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
                               QMenu, QListWidget, QInputDialog, QLineEdit, QListWidgetItem, QMessageBox, QWidget,
                               QScrollArea, QTabWidget, QVBoxLayout)

from ..core import analysis, events, project as pj, quality, single
from ..core.single import parse_names
from .. import theme
from ..theme import C
from .base import BasePage
from .channel_selection import ChannelDialog, ChannelPicker, channel_field
from .common import combo, fspin, line, log
from .widgets import ResultView, StepList, btn, checkbox, hbox, label
from .segment_editor import SegmentEditor

MONTAGES = ["自动识别", "不设置"] + single.STANDARD_MONTAGES
ICA_MODES = {"disabled": "关闭 ICA", "manual": "手动复核", "auto": "自动识别眼电", "keep": "保留全部成分"}
PRE_FIELDS = ("order", "montage", "reref", "reref_channels", "notch", "harmonics", "l_freq", "h_freq", "resample",
              "interpolate", "ica", "ica_n", "ica_method", "ica_auto_eog")


_MINUTE_SECOND = re.compile(r"(\d+)\s*分(?:钟)?\s*(\d+(?:\.\d+)?)\s*秒")
_ENDPOINT = rf"(?:\d+\s*分(?:钟)?\s*\d+(?:\.\d+)?\s*秒|{events._TIME})"
_BAD_RANGE = re.compile(
    rf"(?:从\s*)?第?\s*(?P<start>{_ENDPOINT})"
    rf"(?P<separator>\s*(?:到|至|[~–—,、;])\s*|\s+|(?<=\S)-\s*|\s+-\s+)"
    rf"第?\s*(?P<end>{_ENDPOINT})(?:(?:\s*[,、;]\s*|\s+)(?P<reason>.*))?", re.I)
_RANGE_SYMBOLS = str.maketrans("０１２３４５６７８９：．＋－−，；～", "0123456789:.+--,;~")


def component_indices(text, count=None):
    value = unicodedata.normalize("NFKC", text).strip()
    if value in ("", "无"):
        return []
    result = set()
    for item in parse_names(value):
        match = re.fullmatch(r"(?:ICA?[-_ ]?)?(\d+)", item, re.I)
        if not match:
            raise ValueError(f"“{item}”不是成分编号。请使用整数或 IC-004 这样的编号。")
        index = int(match[1])
        if count is not None and index >= count:
            raise ValueError(f"成分编号应在 0 到 {count - 1} 之间。")
        result.add(index)
    return sorted(result)


def _segment_seconds(cell, unit, sfreq):
    cell = cell.strip().translate(_RANGE_SYMBOLS)
    match = _MINUTE_SECOND.fullmatch(cell)
    if match:
        seconds = events.time_seconds(match[2])
        if seconds >= 60:
            raise ValueError("分、秒写法中的秒数应小于 60")
        return events.time_seconds(match[1], "分钟") + seconds
    return events.time_seconds(cell, unit, sfreq)


def parse_bad_segments(text, duration, sfreq=None):
    """Read review text or two/three spreadsheet columns into second-based intervals."""
    selected = []
    units = ["秒", "秒"]
    first_line = True
    for number, original in enumerate(text.splitlines(), 1):
        if not original.strip():
            continue
        row = original.strip(" ").translate(_RANGE_SYMBOLS)
        columns = re.split(r"[\t,]", row, maxsplit=2)
        if first_line:
            first_line = False
            if (len(columns) >= 2 and re.match(r"(?:开始|起始|起点|start|onset)", columns[0].strip(), re.I)
                    and re.match(r"(?:结束|终止|终点|end|stop)", columns[1].strip(), re.I)):
                units = [events.column_unit(col) or "秒" for col in columns[:2]]
                continue
        try:
            start_unit, end_unit = units
            if "\t" in row:
                cells = original.split("\t", 2)
                start, end = cells[:2]
                reason = cells[2].strip() if len(cells) == 3 else ""
            else:
                match = _BAD_RANGE.fullmatch(row.strip())
                if not match:
                    raise ValueError("请填写开始和结束时间，例如「2 秒至 3.5 秒，眨眼」")
                start, end = match["start"], match["end"]
                reason = original.strip()[match.start("reason"):].strip() if match["reason"] else ""
                separator = match["separator"].strip()
                if separator not in (",", "、", ";"):
                    start_unit = end_unit = "秒"
                if separator in ("到", "至", "-", "~", "–", "—"):
                    # In a range like “100 到 250 毫秒” the trailing unit applies to both ends.
                    trailing = re.fullmatch(rf"{events._NUMBER}\s*({events._TIME_UNIT})", end, re.I)
                    if trailing and re.fullmatch(events._NUMBER, start):
                        start_unit = trailing[1].lower()
            a = _segment_seconds(start, start_unit, sfreq)
            b = _segment_seconds(end, end_unit, sfreq)
            if not 0 <= a < b <= duration + 1e-9:
                raise ValueError(f"开始必须早于结束，且两端应在 0 至 {duration:g} 秒内")
        except ValueError as exc:
            raise ValueError(f"第 {number} 行：{exc}") from exc
        selected.append([a, min(b, duration), reason or "手动"])
    return selected


class PreprocessPage(BasePage):
    title = "预处理"
    subtitle = "选择一个步骤单独运行，或按项目设置完成整条预处理。"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.recs = self.record_picker()
        self.recs.currentIndexChanged.connect(self._select)
        self.steps = StepList("记录准备情况")
        self.steps.section_requested.connect(self.open_step)
        self.mark_segments_btn = btn("标记坏段", lambda: self.review_quality("manual"), None, "mdi6.gesture-swipe-horizontal")
        self.steps.lay.insertLayout(1, hbox(btn("选择通道", self.check_channels, None, "mdi6.format-list-checks"),
                                           self.mark_segments_btn, "stretch"))
        overview = QWidget()
        sheet = QVBoxLayout(overview)
        sheet.setContentsMargins(1, 1, 1, 1)
        step_card = QFrame()
        step_card.setObjectName("Card")
        single_layout = QVBoxLayout(step_card)
        single_layout.setContentsMargins(18, 12, 18, 12)
        single_layout.addWidget(label("单步处理", "H2"))
        self.step_picker = combo(list(pj.SINGLE_STEPS))
        self.step_picker.setAccessibleName("要运行的预处理步骤")
        self.step_source = combo([])
        self.step_source.setAccessibleName("单步输入来源")
        for caption, key in (("自动接续", "auto"), ("原始记录", "raw"), ("当前处理结果", "current")):
            self.step_source.addItem(caption, key)
        self.step_settings_btn = btn("本步参数", self.show_step_settings, None, "mdi6.tune-variant")
        self.run_step_btn = btn("只运行这一步", lambda: self.process_step(self.step_picker.currentText()),
                                "primary", "mdi6.play")
        self.step_fit_btn = btn("先复核 ICA 成分", lambda: self.fit_ica(step_source=self.step_source.currentData()))
        single_layout.addLayout(hbox(self.step_picker, self.step_source, self.step_settings_btn, self.run_step_btn))
        self.step_hint = label("", "Sub", wrap=True)
        single_layout.addWidget(self.step_hint)
        single_layout.addWidget(self.step_fit_btn, 0, Qt.AlignLeft)
        sheet.addWidget(step_card)
        sheet.addWidget(self.steps)
        sheet.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(overview)
        self.work_tabs = QTabWidget()
        self.work_tabs.setAccessibleName("预处理视图")
        self.work_tabs.addTab(scroll, "处理流程")
        self.view = ResultView(QWidget())
        self.work_tabs.addTab(self.view, "检查图与标记")
        self.result_view = ResultView(QWidget())
        self.work_tabs.addTab(self.result_view, "处理结果")
        self.batch_view = ResultView(QWidget())
        self._batch_project = None
        self.work_tabs.setTabEnabled(1, False)
        self.work_tabs.setTabEnabled(2, False)
        self._active_record = None
        self.work_tabs.currentChanged.connect(self._open_result)
        self.body.addWidget(self.work_tabs, 1)
        self.raw = self.ica = self.raw_ica = None
        self._raw_from_single_fit = False
        self._sig = None          # 当前载入的数据对应的设置；设置变了就重新读
        self._non_eeg_hint = {}   # 记录 id → {通道: 原因}
        self.step_picker.currentTextChanged.connect(self._update_step_hint)
        self.step_source.currentIndexChanged.connect(self._update_step_hint)

        s = self.section("当前记录")
        self.cur = s.widget(label("—", "H2", wrap=True))
        self.chan = s.widget(label("非脑电通道：载入后自动识别", "Sub", wrap=True))
        s.widget(btn("检查通道…", self.check_channels, "accent", "mdi6.format-list-checks"))
        self.locs = s.row("电极位置文件（这个被试）", line("", "可不填，自动按标准 10-20 识别"))
        s.widget(btn("选择位置文件…", self._pick_locs, "ghost", "mdi6.folder-open-outline"))
        self.rename = s.widget(checkbox("用位置文件里的通道名"))
        self.rename.setToolTip("按顺序用位置文件里的通道名替换数据里的通道名")
        self.montage = s.row("电极位置", combo(MONTAGES))

        s = self.section("预处理顺序与质量检查")
        s.widget(btn("调整处理顺序…", self.edit_order))
        self.segment_limit = s.row("自动坏段峰峰阈值", fspin(150, 1, 10000, 10, 1, " µV"))
        s.widget(btn("手动编辑坏段…", lambda: self.review_quality("manual")))
        s.widget(btn("自动检测并复核坏段…", lambda: self.review_quality("detect")))
        s.widget(btn("与 AI 助手讨论质量…", lambda: self.review_quality("ai")))
        s = self.section("滤波")
        self.notch = s.row("陷波", fspin(50, 0, 1000, 10, 1, " Hz"))
        self.harm = s.widget(checkbox("同时去除谐波"))
        self.lf = s.row("高通", fspin(0.1, 0, 1000, 0.1, 2, " Hz"))
        self.hf = s.row("低通", fspin(30, 0, 5000, 5, 1, " Hz"))
        self.rs = s.row("重采样", fspin(0, 0, 20000, 50, 1, " Hz"))
        s.widget(label("填 0 表示不做这一步。国内工频 50 Hz。做时频分析时低通要高于关心的频率。", "Muted", wrap=True))

        s = self.section("坏导与参考")
        self.bads = channel_field(s, "坏导", line("", "例：FC5, T8"), "选择坏导…", lambda: self.pick_channels("坏导"))
        s.widget(btn("在插值步骤前检测坏导", self.detect_bads, "accent", "mdi6.radar"))
        self.interp = s.widget(checkbox("插值坏导", True))
        self.reref = s.row("重参考", combo(["不改变", "平均参考", "指定通道"]))
        self.reref_ch = channel_field(s, "参考通道", line("", "例：M1, M2"), "选择参考…", lambda: self.pick_channels("参考通道"))

        s = self.section("ICA 去伪迹")
        self.ica_on = s.widget(checkbox("用 ICA 去除伪迹", True))
        self.ica_n = s.row("成分数", line("", "留空 = 全部"))
        self.ica_m = s.row("算法", combo(["fastica", "infomax", "picard"]))
        self.ica_auto = s.widget(checkbox("自动识别眼电成分", True))
        s.widget(label("没有眼电通道时，用 Fp1、Fp2 等前额电极代替。", "Muted", wrap=True))
        self.excl = s.row("去除成分", line("", "留空 = 用自动识别的结果"))
        self.component_maps = []
        self.excl.textChanged.connect(self.refresh_component_maps)
        s.widget(btn("拟合并查看成分地形图", self.fit_ica))
        s.widget(btn("查看成分波形…", self.browse_components))
        s.widget(btn("确认当前排除清单", self.confirm_components))
        s.widget(btn("让视觉模型看 ICA 成分图…", self.discuss_ica))
        self.end_side()

        more = btn("更多操作", None, None, "mdi6.dots-horizontal")
        menu = QMenu(more)
        menu.addAction("浏览波形", self.browse)
        menu.addAction("拟合 ICA", self.fit_ica)
        menu.addAction("查看成分波形", self.browse_components)
        menu.addAction("用 AI 复核 ICA 成分图…", self.discuss_ica)
        menu.addSeparator()
        menu.addAction("批量处理全部记录", self.process_all)
        more.setMenu(menu)
        self.action_row(more, btn("载入查看", self.preview, None, "mdi6.eye-outline"),
                        btn("处理这条记录", self.process_one, "primary", "mdi6.play"))
        for field in (self.ica_on, self.ica_auto, self.rename, self.interp, self.harm):
            field.clicked.connect(self._save_manual_settings)
        for field in (self.montage, self.reref, self.ica_m):
            field.activated.connect(self._save_manual_settings)
        self._spinners = {self.notch: "notch", self.lf: "l_freq", self.hf: "h_freq", self.rs: "resample",
                          self.segment_limit: "segment_limit_uv"}
        for field, attr in self._spinners.items():
            field.setKeyboardTracking(False)
            field.valueChanged.connect(lambda value, attr=attr: self._save_spinner(attr, value))
        for field in (self.locs, self.bads, self.reref_ch, self.ica_n, self.excl):
            field.editingFinished.connect(lambda field=field: self._save_manual_settings() if field.isModified() else None)

    def open_step(self, name):
        category = {"通道":"当前记录", "滤波":"滤波",
                    "坏段":"预处理顺序与质量检查", "坏导":"坏导与参考", "ICA":"ICA 去伪迹"}
        target = {"通道":"通道", "坏导":"坏道插值", "ICA":"ICA"}.get(name)
        if name == "滤波" and self.step_picker.currentText() not in pj.STEPS:
            target = self.project.pipeline.order[0]
        if target:
            self.step_picker.setCurrentText(target)
        self.group_picker.setCurrentText(category[name])
        self.show_settings(True)

    def show_step_settings(self):
        name = self.step_picker.currentText()
        category = {"通道":"当前记录", "坏道插值":"坏导与参考", "重参考":"坏导与参考", "ICA":"ICA 去伪迹"}
        self.group_picker.setCurrentText(category.get(name, "滤波"))
        self.show_settings(True)

    # ------------------------------------------------------------ 设置 <-> 界面
    def _load_pipe(self):
        p = self.project.pipeline
        blockers = [QSignalBlocker(field) for field in self._spinners]
        self.montage.setCurrentText(p.montage)
        self.reref.setCurrentText(p.reref)
        self.reref_ch.setText(p.reref_channels)
        self.notch.setValue(p.notch)
        self.harm.setChecked(p.harmonics)
        self.lf.setValue(p.l_freq)
        self.hf.setValue(p.h_freq)
        self.rs.setValue(p.resample)
        self.interp.setChecked(p.interpolate)
        self.ica_on.setChecked(p.ica)
        self.ica_n.setText(p.ica_n)
        self.ica_m.setCurrentText(p.ica_method)
        self.ica_auto.setChecked(p.ica_auto_eog)
        self.segment_limit.setValue(p.segment_limit_uv)

    def _commit(self, changes, affected):
        """Publish metadata and its derived-status changes together; undo memory edits on I/O failure."""
        changes = [(obj, name, value) for obj, name, value in changes if getattr(obj, name) != value]
        if not changes:
            return True
        dirty = {}
        for subj, rec in affected:
            dirty[subj.name] = subj
            changes.append((rec, "status", pj.REC_RAW))
            changes.append((rec, "record_epochs_file", ""))
        for subj in dirty.values():
            changes.extend([(subj, "status", pj.STATUS_RAW), (subj, "epochs_file", ""), (subj, "n_trials", {})])
        previous = [(obj, name, getattr(obj, name)) for obj, name, _ in changes]
        try:
            for obj, name, value in changes:
                setattr(obj, name, value)
            self.project.save()
        except (ValueError, OSError) as exc:
            for obj, name, value in previous:
                setattr(obj, name, value)
            QMessageBox.warning(self, "项目修改未保存", str(exc))
            return False
        for name in dirty:
            self.project._cache.pop(name, None)
        self.ctx.statusBar().showMessage("项目修改已保存", 3000)
        return True

    def _save_spinner(self, attr, value):
        cur = self._current()
        if (not self.project or self.ctx.runner.busy or self._active_record !=
                ((self.project, cur[1].id) if cur else None) or getattr(self.project.pipeline, attr) == value):
            return
        affected = [] if attr == "segment_limit_uv" else self.project.recordings()
        if not self._commit([(self.project.pipeline, attr, value)], affected):
            self._load_pipe()
            return
        if cur and attr != "segment_limit_uv":
            self.raw = self.ica = self.raw_ica = self._sig = None
            self.component_maps = []
            self.work_tabs.setTabEnabled(1, False)
            self._update_steps()

    def _save_manual_settings(self, *_):
        cur = self._current()
        if (self.project is None or self.ctx.runner.busy or self._active_record !=
                ((self.project, cur[1].id) if cur else None)):
            return
        if not self._save_pipe():
            self._load_pipe()
            self._select()

    def _save_pipe(self):
        p = self.project.pipeline
        values = dict(montage=self.montage.currentText(), reref=self.reref.currentText(), reref_channels=self.reref_ch.text(),
                      notch=self.notch.value(), harmonics=self.harm.isChecked(), l_freq=self.lf.value(),
                      h_freq=self.hf.value(), resample=self.rs.value(), interpolate=self.interp.isChecked(),
                      ica=self.ica_on.isChecked(), ica_n=self.ica_n.text().strip(), ica_method=self.ica_m.currentText(),
                      ica_auto_eog=self.ica_auto.isChecked())
        changes = [(p, name, val) for name, val in values.items() if getattr(p, name) != val]
        affected = self.project.recordings() if changes else []
        changes.append((p, "segment_limit_uv", self.segment_limit.value()))
        cur = self._current()
        if cur is not None:
            s, r = cur
            if s.locs != self.locs.text().strip() or s.rename_from_locs != self.rename.isChecked():
                changes += [(s, "locs", self.locs.text().strip()), (s, "rename_from_locs", self.rename.isChecked())]
                if not affected:
                    affected = [(s, item) for item in s.recordings]
            record_edits = [(r, "bads", single.channel_names(self.bads.text()))]
            ex = self.excl.text().strip()
            if not self.needs_component_review(r):
                try:
                    marks = component_indices(ex, self.ica.n_components_ if self.ica is not None else None)
                except ValueError as exc:
                    QMessageBox.warning(self, "排除清单未保存", str(exc))
                    return False
                record_edits.append((r, "ica_manual", bool(ex)))
                if ex:
                    record_edits.append((r, "ica_exclude", marks))
                if not ex or marks != r.ica_exclude:
                    has_fit = self.ica is not None and self.fit_settings == self.fit_controls()
                    record_edits.append((r, "reviewed_ica", self.fit_identity if ex and has_fit else ""))
            if any(getattr(obj, attr) != value for obj, attr, value in record_edits):
                changes.extend(record_edits)
                if not affected:
                    affected = [cur]
        if not self._commit(changes, affected):
            return False
        if cur:
            if self.raw is not None and self._signature(*cur) != self._sig:
                self.raw = self.ica = self.raw_ica = self._sig = None
                self.component_maps = []
                self.work_tabs.setTabEnabled(1, False)
            if self.ica is not None and self.fit_settings == self.fit_controls():
                self.ica.exclude = sorted(self.marked_components())
            self._update_steps()
        return True

    def _signature(self, s, r):
        """决定“载入的数据还能不能直接用”的所有设置。坏导、通道、滤波任一变化都要重新读。"""
        p = self.project.pipeline
        return json.dumps([r.id, r.bads, r.bad_segments, r.non_eeg, s.locs, s.rename_from_locs,
                           {k: getattr(p, k) for k in PRE_FIELDS if not k.startswith("ica")}],
                          ensure_ascii=False, sort_keys=True)

    # ------------------------------------------------------------ 显示
    def _current(self):
        rid = self.recs.currentData()
        if rid is None or self.project is None:
            return None
        try:
            return self.project.get_rec(rid)
        except KeyError:
            return None

    def _still_selected(self, project, record, signature=None):
        cur = self._current()
        return (self.project is project and cur is not None and cur[1] is record
                and (signature is None or self._signature(*cur) == signature))

    def on_show(self, subject=None):
        super().on_show()
        p = self.project
        if self._batch_project is not None and self._batch_project is not p:
            if self.work_tabs.currentWidget() is self.batch_view:
                self.work_tabs.setCurrentIndex(0)
            self.work_tabs.removeTab(self.work_tabs.indexOf(self.batch_view))
            self.batch_view.reset()
            self._batch_project = None
        self.recs.blockSignals(True)
        cur = subject or self.recs.currentData()
        self.recs.clear()
        if p is not None:
            self._load_pipe()
            for s, r in p.recordings():
                self.recs.addItem(r.id, r.id)
                index = self.recs.count() - 1
                self.recs.setItemData(index, r.source, Qt.ToolTipRole)
            index = self.recs.findData(cur) if cur else -1
            if index < 0 and subject:
                index = next((i for i, (subj, _) in enumerate(p.recordings()) if subj.name == subject), -1)
            self.recs.setCurrentIndex(max(0, index) if self.recs.count() else -1)
        self.recs.blockSignals(False)
        self._select()

    def _select(self, *_):
        cur = self._current()
        record_key = (self.project, cur[1].id) if cur else None
        if record_key != self._active_record:
            if self._active_record and self._active_record[0] is not self.project:
                self._non_eeg_hint.clear()
            self._active_record = record_key
            self.work_tabs.setCurrentIndex(0)
            self.view.reset()
            self.result_view.reset()
            self.work_tabs.setTabEnabled(1, False)
            self.component_maps.clear()
            self.raw = self.ica = self.raw_ica = self._sig = None
        self.recs.setToolTip("\n".join(filter(None, (self.recs.currentText(), self.recs.currentData(Qt.ToolTipRole)))))
        if cur is None:
            self.work_tabs.setTabEnabled(2, False)
            self._update_step_hint()
            self.cur.setText("—")
            self.steps.status.clear()
            self.steps.set([("off", "提示", "项目里还没有原始数据。到「项目」页导入文件或文件夹。", "")])
            return
        s, r = cur
        self.cur.setText(r.id)
        self.bads.setText(single.format_channels(r.bads))
        self.locs.setText(s.locs)
        self.rename.setChecked(s.rename_from_locs)
        self.excl.setText((", ".join(map(str, r.ica_exclude)) or "无")
                          if r.ica_manual and not self.needs_component_review(r) else "")
        self.excl.setPlaceholderText("留空 = 用自动识别的结果")
        self._update_chan_label(r)
        self._update_steps()

    def show_inspection(self, result):
        self.view.show_result(result)
        self.work_tabs.setTabEnabled(1, True)
        self.work_tabs.setCurrentIndex(1)

    def _open_result(self, index):
        if index != 2 or self.result_view.widget() is not self.result_view.empty:
            return
        cur = self._current()
        if not cur or not pj.has_continuous_result(self.project, cur[1]):
            return
        project, record = self.project, cur[1]
        rel = record.clean_file
        path = project.path(rel)

        def load():
            import mne
            with mne.io.read_raw_fif(path, preload=False, verbose=False) as raw:
                return self._clean_result(record, raw)

        def done(result):
            if self._still_selected(project, record) and record.clean_file == rel:
                self.result_view.show_result(result)
        self.run(f"读取 {record.id} 的处理结果", load, done)

    def _update_chan_label(self, r):
        if r.non_eeg is None:
            hint = self._non_eeg_hint.get(r.id)
            self.chan.setText("非脑电通道：载入后自动识别" if hint is None else
                              f"自动识别出 {len(hint)} 个非脑电通道，处理时会去掉（点“检查通道”确认）")
        else:
            self.chan.setText(f"去掉 {len(r.non_eeg)} 个非脑电通道（已确认）" if r.non_eeg else "所有通道都当作脑电（已确认）")

    def _update_steps(self):
        cur = self._current()
        if cur is None:
            return
        s, r = cur
        p = self.project.pipeline
        saved = pj.has_continuous_result(self.project, r)
        partial = saved and bool(r.step_history) and r.status != pj.REC_DONE
        done = saved and r.status == pj.REC_DONE
        self.steps.status.setText("已单步处理" if partial else "已处理" if done else "待重新处理" if r.clean_file else "待处理")
        self._update_step_hint()
        if not saved and self.work_tabs.currentIndex() == 2:
            self.work_tabs.setCurrentIndex(0)
        if not saved and self.result_view.widget() is not self.result_view.empty:
            self.result_view.reset()
        self.work_tabs.setTabEnabled(2, saved)
        applied = [item["step"] for item in r.step_history] if partial else []
        loaded = self.raw is not None and not self._raw_from_single_fit
        hint = self._non_eeg_hint.get(r.id)
        if "通道" in applied:
            ch = ("done", "已保存选定通道的连续数据", "已单独运行")
        elif r.non_eeg is not None:
            ch = ("done", f"去掉 {len(r.non_eeg)} 个非脑电通道" if r.non_eeg else "全部通道都是脑电", "已确认")
        elif hint is not None:
            ch = ("wait", f"自动识别出 {len(hint)} 个非脑电通道", "待确认")
        else:
            ch = ("done", "按通道类型和名称自动筛选", "已处理") if done else ("todo", "载入时自动识别非脑电通道", "")
        bads = ("done" if r.bads else "todo",
                ("坏导 " + ", ".join(r.bads) + ("，插值" if p.interpolate else "，只标记")) if r.bads
                else "尚未标记坏导", "已标记" if r.bads else "")
        if p.ica and self.needs_component_review(r):
            ica = ("warn", "分解已变化，重新处理前请确认当前成分", "待复核")
        elif done or "ICA" in applied:
            history = r.processed_ica
            if history:
                ids = "、".join(f"IC-{i:03d}" for i in history["exclude"])
                method = ICA_MODES[history["mode"]]
                ica = ("done", f"{method}，已去除 {ids}" if ids else f"{method}，未去除成分", "已处理")
            else:
                ica = ("done", "历史结果未保存实际去除的成分清单", "已处理")
        elif not p.ica:
            ica = ("off", "已关闭 ICA 去伪迹", "")
        elif r.ica_manual:
            ids = "、".join(f"IC-{i:03d}" for i in r.ica_exclude)
            if ids:
                ica = ("wait", "已标记排除 " + ids, "待处理")
            else:
                ica = ("done", "已确认保留所有成分", "已确认")
        elif self.ica is not None:
            ids = "、".join(f"IC-{i:03d}" for i in getattr(self, "fit_auto", ()))
            ica = ("wait", f"自动建议 {ids}" if ids else "已拟合，可在检查图中复核成分", "待复核")
        else:
            ica = ("todo", "处理时拟合，自动识别眼电成分" if p.ica_auto_eog else "拟合后可手动指定去除成分", "")
        self.steps.set([
            (ch[0], "通道", ch[1] + "。核对参与分析的电极。", ch[2]),
            ("done" if r.bad_segments else "todo", "坏段",
             f"已标记 {len(r.bad_segments)} 个区间。分段时会跳过与这些区间重叠的试次。", ""),
            ("done" if done or loaded else "todo", "滤波", " → ".join(p.order) + "。按设定顺序调整频段、采样率和参考。",
             "已运行 " + "、".join(k for k in applied if k in pj.STEPS) if any(k in pj.STEPS for k in applied) else ""),
            (bads[0], "坏导", bads[1] + "。可根据其余电极插值估计坏导信号。", bads[2]),
            (ica[0], "ICA", ica[1] + "。结合地形图和时间波形判断眼电等伪迹。", ica[2]),
            ("done" if saved else "todo", "保存", ("单步结果已保存，可继续运行其他步骤或直接分段。" if partial
             else "已生成连续数据，可查看处理结果或继续分段。") if saved
             else "处理后将连续脑电保存为 FIF，供后续分段使用。", "已保存" if saved else ""),
        ])

    def _update_step_hint(self, *_):
        cur = self._current()
        step = self.step_picker.currentText()
        self.step_fit_btn.setVisible(step == "ICA" and cur is not None)
        source = self.step_source.currentData()
        if not cur:
            self.run_step_btn.setEnabled(False)
            self.step_hint.setText("导入连续记录后即可单步处理。")
            return
        rec = cur[1]
        valid = pj.has_continuous_result(self.project, rec)
        history = rec.step_history
        self.run_step_btn.setEnabled(source != "current" or (valid and step != "通道"))
        if step == "通道" and source == "current":
            message = "通道选择从原始记录读取，请将输入切换为原始记录或自动接续。"
        elif source == "current" and not valid:
            message = "请先生成处理结果，或选择原始记录作为输入。"
        elif source == "raw" or step == "通道":
            message = "输入为原始记录。"
        elif any(item["step"] == step for item in history):
            message = "将用上次执行此步骤前的数据重新计算，后续步骤可按需再运行。"
        elif history:
            message = f"输入为上一步「{history[-1]['step']}」的输出。"
        else:
            message = "输入为当前保存的连续数据。" if source == "current" else "输入为原始记录。"
        if history:
            message += " 已运行：" + " → ".join(item["step"] for item in history) + "。"
        self.step_hint.setText(message)

    def edit_order(self):
        if not self.need_project(need_done=False):
            return
        p = self.project
        dialog = QDialog(self)
        dialog.setWindowTitle("拖动排列步骤 · ICA 在末尾执行")
        layout = QVBoxLayout(dialog)
        steps = QListWidget()
        steps.addItems(p.pipeline.order)
        steps.setDragDropMode(QAbstractItemView.InternalMove)
        layout.addWidget(steps)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() and self.project is p:
            order = [steps.item(i).text() for i in range(steps.count())]
            if p.pipeline.order != order:
                if not self._commit([(p.pipeline, "order", order)], p.recordings()):
                    return
                self.raw = self.ica = self.raw_ica = self._sig = None
                self._update_steps()

    def review_quality(self, mode):
        cur = self._need_rec()
        if not cur:
            return
        subj, rec = cur
        if not self._save_pipe():
            return
        project = self.project
        sig = self._signature(subj, rec)
        limit = self.segment_limit.value()

        def work():
            raw, _ = pj.open_raw(subj, rec, project.pipeline, log=log)
            try:
                if mode == "manual":
                    return raw, [], {}
                candidates, summary = quality.inspect_raw(raw, limit)
                pipe = project.pipeline
                summary.update({"执行顺序": pipe.order, "陷波Hz": pipe.notch, "高低通Hz": [pipe.l_freq, pipe.h_freq],
                                "重采样Hz": pipe.resample, "重参考": pipe.reref, "已标坏导数": len(rec.bads),
                                "插值": pipe.interpolate, "ICA": pipe.ica, "已确认坏段数": len(rec.bad_segments)})
                return raw, candidates, summary
            except Exception:
                raw.close()
                raise

        def done(result):
            raw, candidates, summary = result
            if not self._still_selected(project, rec, sig) or mode == "ai":
                raw.close()
                if mode == "ai" and self._still_selected(project, rec, sig):
                    self.ctx.discuss_quality(summary)
                return

            def save(selected):
                if not self._still_selected(project, rec, sig):
                    QMessageBox.warning(self, "坏段未保存", "记录或标记已发生变化，请重新打开波形复核。")
                    return False
                if sorted(selected) == sorted(rec.bad_segments):
                    return True
                if not self._commit([(rec, "bad_segments", selected)], [(subj, rec)]):
                    return False
                self.raw = self.ica = self.raw_ica = self._sig = None
                self.component_maps = []
                self.work_tabs.setTabEnabled(1, False)
                self.view.reset()
                self._update_steps()
                self.ctx.statusBar().showMessage(f"{rec.id} · 已保存 {len(selected)} 个坏段", 6000)
                return True

            try:
                dialog = SegmentEditor(self, raw, rec, candidates, limit, save, parse_bad_segments)
            except Exception:
                raw.close()
                raise
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.finished.connect(lambda *_: raw.close())
            dialog.open()
        return self.run("读取原始波形" if mode == "manual" else "检测坏段候选", work, done)

    def _pick_locs(self):
        p, _ = QFileDialog.getOpenFileName(self, "电极位置文件", os.path.expanduser("~/Desktop"),
                                           "电极位置 (*.locs *.loc *.elc *.sfp *.elp *.txt *.csd *.bvef);;所有文件 (*)")
        if p:
            self.locs.setText(os.path.normpath(p))
            self._save_manual_settings()

    def _need_rec(self):
        if not self.need_project(need_done=False):
            return None
        cur = self._current()
        if cur is None:
            QMessageBox.information(self, "没有原始数据", "请先在「项目」页导入原始数据（文件或文件夹）。")
            return None
        return cur

    # ------------------------------------------------------------ 操作
    def _load(self, s, r, pipe, with_figs=True):
        """后台读取、设置通道并滤波（坏导按设置插值）。返回 (raw, 非脑电通道建议, 检查结果)。"""
        hint = {}
        raw, dropped = pj.open_raw(s, r, pipe, log=log, hint_out=hint)
        res = analysis.Result("预处理", f"{s.name}" + (f" · {r.condition}" if r.condition else "") + " · 检查")
        if with_figs:
            res.figures.append(("滤波前功率谱", single.plot_psd(raw, 0, 80)))
        pj.filter_raw(raw, pipe, log=log)
        if with_figs:
            res.figures.append(("滤波后功率谱", single.plot_psd(raw, 0, min(80, raw.info["sfreq"] / 2))))
            if raw.get_montage() is not None:
                res.figures.append(("电极位置", single.plot_sensors(raw)))
        res.metrics = [("脑电通道", str(len(raw.ch_names)), f"去掉 {len(dropped)} 个非脑电通道"),
                       ("采样", f"{raw.info['sfreq']:g} Hz", f"{raw.times[-1]:.0f} 秒"),
                       ("电极位置", "已设置" if raw.get_montage() is not None else "未设置",
                        "坏导 " + (", ".join(r.bads) or "无"))]
        return raw, hint, res

    def preview(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        if not self._save_pipe():
            return
        project = self.project
        sig = self._signature(s, r)

        def done(out):
            if not self._still_selected(project, r, sig):
                return
            self.raw, hint, res = out
            self._raw_from_single_fit = False
            self._non_eeg_hint[r.id] = hint
            self.ica = self.raw_ica = None
            self._sig = sig
            self.show_inspection(res)
            self._update_chan_label(r)
            self._update_steps()

        self.run(f"载入 {r.id}", lambda: self._load(s, r, project.pipeline), done)

    def check_channels(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        project = self.project
        sig = self._signature(s, r)

        def read_channels(sub, rec):
            raw, _ = pj.reading.read_raw_any(rec.source, log=log)
            try:
                if sub.locs:
                    single.apply_channel_setup(raw, "file", sub.locs, sub.rename_from_locs, None, log=log)
                pj._auto_types(raw)
                return raw.ch_names, raw.get_channel_types(), pj.suggest_non_eeg(raw)
            finally:
                raw.close()

        def done(out):
            if not self._still_selected(project, r, sig):
                return
            names, types, hint = out
            self._non_eeg_hint[r.id] = hint
            current = r.non_eeg if r.non_eeg is not None else list(hint)
            d = ChannelDialog(self, names, types, hint, current, len(self.project.recordings()))
            if d.exec() and self._still_selected(project, r, sig):
                non = d.non_eeg()
                edits = [(s,r,non)] if r.non_eeg != non else []
                added, removed = set(non) - set(current), set(current) - set(non)
                def save(rows):
                    if not self._still_selected(project, r, sig):
                        return
                    if not self._commit([(rec,"non_eeg",values) for _,rec,values in rows],
                                        [(sub,rec) for sub,rec,_ in rows]):
                        return
                    if any(rec is r for _,rec,_ in rows):
                        self.raw = self.ica = self.raw_ica = self._sig = None
                    self._update_chan_label(r)
                    self._update_steps()
                    log(f"{r.id} 已确认排除 {len(non)} 个通道，更新了 {len(rows)} 条记录")
                if d.apply_all.isChecked() and (added or removed):
                    def matches():
                        rows = list(edits)
                        for sub, rec in project.recordings():
                            if rec is r:
                                continue
                            other_names, _, auto = read_channels(sub,rec)
                            known = set(other_names)
                            previous = list(auto) if rec.non_eeg is None else rec.non_eeg
                            new = [n for n in previous if n not in removed & known]
                            new.extend(n for n in other_names if n in added and n not in new)
                            if new != previous:
                                rows.append((sub,rec,new))
                        return rows
                    self.ctx.runner.run("匹配其他记录的同名通道", matches, save)
                else:
                    save(edits)
            self._update_chan_label(r)
            self._update_steps()

        return self.ctx.runner.run(f"读取 {r.id} 的通道", lambda: read_channels(s,r), done)

    def pick_channels(self, purpose):
        cur = self._need_rec()
        if not cur or not self._save_pipe():
            return
        subject, rec = cur
        project = self.project
        sig = self._signature(subject, rec)
        editor = self.bads if purpose == "坏导" else self.reref_ch
        before = editor.text()
        source = self.step_source.currentData()
        def read():
            raw = pj.open_step_input(project, subject, rec, "坏道插值" if purpose == "坏导" else "重参考",
                                     source=source, log=log)
            try:
                pairs = [(n,t) for n,t in zip(raw.ch_names, raw.get_channel_types()) if t == "eeg"]
                return [n for n,_ in pairs], [t for _,t in pairs]
            finally:
                raw.close()
        def show(result):
            if not self._still_selected(project, rec, sig) or self.ctx.stack.currentWidget() is not self:
                return
            names, types = result
            verb = "勾选要标记为坏导的通道。" if purpose == "坏导" else "勾选参考电极。确认后使用「指定通道」重参考，清空将切换到「不改变」。"
            dialog = ChannelPicker(self, "选择" + purpose, f"{rec.id} · {verb}", names, types, single.channel_names(before))
            if not dialog.exec() or not self._still_selected(project, rec, sig) or before != editor.text():
                return
            selected = dialog.selected_names()
            if purpose == "参考通道" and not selected:
                with QSignalBlocker(self.reref):
                    self.reref.setCurrentText("不改变")
            elif purpose == "参考通道":
                with QSignalBlocker(self.reref):
                    self.reref.setCurrentText("指定通道")
            editor.setText(single.format_channels(selected))
            self._save_manual_settings()
        return self.ctx.runner.run("读取可选" + purpose, read, show)

    def detect_bads(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        if not self._save_pipe():
            return
        project = self.project
        sig = self._signature(s, r)
        pipe = self.project.pipeline

        def work():
            raw, _ = pj.open_raw(s, r, pipe, log=log)
            raw.info["bads"] = []
            pj.filter_raw(raw, pipe, log=log, interpolate=False, stop_before='坏道插值')
            return pj.suggest_bads(raw)

        def done(sug):
            if not self._still_selected(project, r, sig):
                return
            res = analysis.Result("预处理", f"{r.id} · 坏导检测")
            if sug:
                res.table = (["通道", "原因"], [[k, v] for k, v in sug.items()])
                res.table_title = "建议标为坏导"
                res.notes = ["建议已填入「参数设置 → 坏导与参考」，可编辑或打开波形逐个确认。"]
                self.bads.setText(single.format_channels(dict.fromkeys([*single.channel_names(self.bads.text()), *sug])))
            else:
                res.notes = ["没有发现明显的坏导。"]
            res.metrics = [("建议坏导", str(len(sug)), "整段数据按 2 秒小窗和其他电极比较")]
            if self._save_pipe():
                self.show_inspection(res)
            else:
                self._select()

        self.run(f"检测 {r.id} 的坏导", work, done)

    def browse(self):
        if self.raw is None:
            QMessageBox.information(self, "先载入", "先点「载入查看」。")
            return
        s, r = self._current()
        try:
            raw = self.raw.copy()
            project = self.project
            base_bads = set(r.bads)
            base_segments = [list(row) for row in r.bad_segments]
            # Interpolation can clear MNE's bad-channel flags in the cached preview.
            shown_channels = set(raw.ch_names)
            raw.info["bads"] = sorted(base_bads & shown_channels)

            def intervals():
                return {(round(float(a - raw.first_time), 7), round(float(d), 7), str(n))
                        for a, d, n in zip(raw.annotations.onset, raw.annotations.duration, raw.annotations.description)
                        if d > 0}

            original = intervals()
            b = raw.plot(duration=10, n_channels=min(32, len(raw.ch_names)), block=False, show=True,
                         theme=theme.BROWSER, title=f"{r.id} · 点通道名切换坏导，按 A 拖选坏段，关闭窗口时保存")
            dock = getattr(getattr(b, "mne", None), "fig_annotation", None)
            if hasattr(dock, "_add_description"):
                default_label = "BAD_qc_手动"
                if dock.description_cmbx.findText(default_label) < 0:
                    dock._add_description(default_label)
                else:
                    dock.description_cmbx.setCurrentText(default_label)

            def closed():
                final = intervals()
                removed = original - final
                segments = [[a, b, name] for a, b, name in base_segments
                            if (round(a, 7), round(b - a, 7), "BAD_qc_" + name) not in removed]
                for start, duration, label in sorted(final - original):
                    segments.append([max(0, start), round(start + duration, 7), label.removeprefix("BAD_qc_")])
                segments.sort()
                bads = (base_bads - shown_channels) | set(raw.info["bads"])
                if sorted(segments) == sorted(base_segments) and bads == base_bads:
                    return
                if (self.project is not project or self.ctx.runner.busy or
                        r.bad_segments != base_segments or set(r.bads) != base_bads):
                    QMessageBox.warning(self, "波形标记未保存", f"{r.id} 的项目、标记或运行状态已变化，请重新打开波形复核。")
                    return False
                if not self._commit([(r, "bad_segments", segments), (r, "bads", sorted(bads))], [(s, r)]):
                    return False
                if self._current() and self._current()[1] is r:
                    self.bads.setText(single.format_channels(r.bads))
                    self.raw = self.ica = self.raw_ica = self._sig = None
                    self.work_tabs.setCurrentIndex(0)
                    self.work_tabs.setTabEnabled(1, False)
                    self.view.reset()
                    self.component_maps = []
                    self._update_steps()
                message = f"{r.id} · 已保存 {len(bads)} 个坏导和 {len(segments)} 个坏段，处理这条记录后生效。"
                self.ctx.statusBar().showMessage(message)
                log(message)
            self.ctx.keep_window(b, closed, record_id=r.id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "波形浏览器", str(e))

    def fit_ica(self, *, step_source=None):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        if not self._save_pipe():
            return
        pipe = self.project.pipeline
        sig = self._signature(s, r)
        reuse = self.raw if (step_source is None and not self._raw_from_single_fit and self._sig == sig) else None
        fit_settings = self.fit_controls()
        fit_project = self.project

        def work():
            raw = reuse
            hint = None
            if step_source is not None:
                raw = pj.open_step_input(fit_project, s, r, "ICA", source=step_source, log=log)
            elif raw is None:
                raw, hint, _ = self._load(s, r, pipe, with_figs=False)
            ica, raw_ica = pj.fit_ica(raw, pipe, log=log)
            sug = pj.auto_exclude(ica, raw, pipe, log=log)
            res = analysis.Result("预处理", f"{r.id} · ICA 成分")
            try:
                # MNE's own title-click handler edits the object that made the figure.
                # Bind clicks on both head and title to the project transaction below.
                figs = ica.copy().plot_components(show=False)
                figs = figs if isinstance(figs, list) else [figs]
                for i, f in enumerate(figs):
                    res.figures.append((f"成分地形图 {i + 1}", f))
                if sug:
                    for i, f in enumerate(ica.plot_properties(raw, picks=sug[:3], show=False)):
                        res.figures.append((f"成分 {sug[i]} 属性", f))
                    res.figures.append(("去除前后对比", ica.plot_overlay(raw_ica, exclude=sug, show=False)))
            except (ValueError, RuntimeError) as exc:
                log(f"部分 ICA 图不可用：{exc}")
            res.metrics = [("成分数", str(ica.n_components_), pipe.ica_method),
                           ("自动建议", ", ".join(map(str, sug)) or "未识别到", "与眼电或前额电极相关的成分")]
            res.notes = ["点击头皮图或成分编号即可标记排除，再点一次可撤销。标记保存在这条记录中，重新处理后写入连续脑电。"
                         "需要对照时间变化时，可从「更多操作」打开成分波形，关闭波形窗口时保存。"]
            return raw, ica, raw_ica, sug, res, hint

        def done(out):
            cur = self._current()
            if (self.project is not fit_project or not cur or self._signature(*cur) != sig
                    or self.fit_controls() != fit_settings):
                log("拟合期间记录或设置已变化，请重新拟合。")
                return
            self.raw, self.ica, self.raw_ica, sug, res, hint = out
            self._raw_from_single_fit = step_source is not None
            if hint is not None:
                self._non_eeg_hint[r.id] = hint
            self._sig = sig
            self.ai_fit_new(sug)
            if self.needs_component_review(r):
                self.excl.clear()
                note = "当前拟合与上次复核不同。请重新选择成分，或在 ICA 设置里确认当前排除清单，然后再处理记录。"
                res.notes.insert(0, note)
                log(note)
            if sug:
                self.excl.setPlaceholderText(f"留空 = 自动：{', '.join(map(str, sug))}")
            self.bind_component_maps(res)
            self.show_inspection(res)
            self.refresh_component_maps()
            self._update_steps()

        return self.run(f"{r.id} 拟合 ICA", work, done)

    def fit_controls(self):
        return (self.notch.value(),self.harm.isChecked(),self.lf.value(),self.hf.value(),self.rs.value(),
                self.reref.currentText(),self.reref_ch.text(),self.interp.isChecked(),self.bads.text(),self.locs.text(),
                self.montage.currentText(),self.rename.isChecked(),self.ica_n.text(),self.ica_m.currentText(),
                self.ica_auto.isChecked(),self.ica_on.isChecked())

    def ai_fit_new(self, suggested):
        from uuid import uuid4
        self.fit_token=uuid4().hex
        self.fitted_ica=self.ica
        self.fit_project=self.project
        self.fit_settings=self.fit_controls()
        self.fit_auto=list(suggested)
        self.fit_identity = pj.ica_identity(self.ica)

    def needs_component_review(self, rec):
        return (bool(rec.reviewed_ica) and self.ica is not None and self.ica is getattr(self, "fitted_ica", None)
                and self.fit_project is self.project and rec.reviewed_ica != self.fit_identity)

    def check_fit(self,token):
        cur=self._current()
        if (not cur or self.ica is None or self.project is not getattr(self,"fit_project",None) or self.ica is not getattr(self,"fitted_ica",None)
                or token!=getattr(self,"fit_token",None) or self.fit_settings!=self.fit_controls()
                or self._sig!=self._signature(*cur)):
            raise ValueError("这张图的 ICA 分解已失效。请在当前记录重新拟合 ICA，再生成成分图。")
        return cur

    def marked_components(self):
        value=self.excl.text().strip()
        return set(self.fit_auto if not value else component_indices(value, self.ica.n_components_))

    def set_component_marks(self, token, marks):
        from ..core.workbench import stamp
        from .project_bridge import apply_selected

        _, rec = self.check_fit(token)
        wanted = set(marks)
        if any(type(index) is not int or not 0 <= index < self.ica.n_components_ for index in wanted):
            raise ValueError("成分编号超出了这次拟合的范围。")
        current = self.marked_components()
        changed = current ^ wanted
        if (not changed and rec.reviewed_ica == self.fit_identity and rec.ica_manual
                and set(rec.ica_exclude) == wanted):
            return
        # An explicit confirmation can also adopt the automatically suggested set unchanged.
        edits = [{"kind": "ica", "fit": token, "record": rec.id, "component": idx,
                  "base": stamp(idx in current), "after": idx in wanted} for idx in sorted(changed or {0})]
        apply_selected(self.ctx, edits, {})
        self.refresh_component_maps()
        self._update_steps()
        message = f"{rec.id} · 已保存 {len(wanted)} 个排除标记，处理这条记录后生效。"
        self.ctx.statusBar().showMessage(message)
        log(message)

    def confirm_components(self):
        try:
            token = getattr(self, "fit_token", None)
            self.check_fit(token)
            self.set_component_marks(token, self.marked_components())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法确认成分标记", str(exc))

    def bind_component_maps(self, result):
        token = self.fit_token
        self.component_maps = []
        for _, fig in result.figures:
            axes = {ax: int(match[1]) for ax in fig.axes if (match := re.fullmatch(r"ICA(\d+)", ax.get_label()))}
            if not axes:
                continue
            self.component_maps.append((fig, axes))

            def clicked(event, *, axes=axes, token=token):
                if event.button != 1 or event.dblclick or getattr(getattr(event.canvas, "toolbar", None), "mode", ""):
                    return
                index = axes.get(event.inaxes)
                if index is None:
                    index = next((idx for ax, idx in axes.items() if ax.title.contains(event)[0]), None)
                if index is None:
                    return
                try:
                    self.check_fit(token)
                    self.set_component_marks(token, self.marked_components() ^ {index})
                except (ValueError, OSError) as exc:
                    self.refresh_component_maps()
                    QMessageBox.warning(self, "无法保存成分标记", str(exc))

            fig.canvas.mpl_connect("button_press_event", clicked)

    def refresh_component_maps(self, *_):
        if self.ica is None or self.ica is not getattr(self, "fitted_ica", None):
            return
        try:
            marked = self.marked_components()
        except ValueError:
            return
        for fig, axes in self.component_maps:
            for ax, idx in axes.items():
                ax.title.set_text(f"ICA{idx:03d}" + (" · 排除" if idx in marked else ""))
                ax.title.set_color("#b65b35" if idx in marked else ax.xaxis.label.get_color())
            try:
                fig.canvas.draw_idle()
            except RuntimeError:
                pass  # The Qt canvas was disposed when an inspection tab or a zoom dialog closed.

    def browse_components(self):
        try:
            token = getattr(self, "fit_token", None)
            _, rec = self.check_fit(token)
            original = self.marked_components()
            viewer_ica = self.ica.copy()
            viewer_ica.exclude = sorted(original)
            browser = viewer_ica.plot_sources(self.raw_ica, block=False, show=True, theme=theme.BROWSER,
                                               title=f"{rec.id} · 点击成分名切换排除，关闭窗口时保存")

            def closed():
                final = set(viewer_ica.exclude)
                if final == original:
                    return
                try:
                    self.check_fit(token)
                    # Apply only edits made in this viewer. The assistant or another figure
                    # may have updated other components while the window was open.
                    merged = (self.marked_components() | (final - original)) - (original - final)
                    self.set_component_marks(token, merged)
                except (ValueError, OSError) as exc:
                    QMessageBox.warning(self, "无法保存成分标记", str(exc))
                    return False

            self.ctx.keep_window(browser, closed, record_id=rec.id)
        except (ValueError, RuntimeError, OSError) as exc:
            QMessageBox.information(self, "成分波形", str(exc))

    def discuss_ica(self):
        try:
            token=getattr(self,"fit_token",None)
            subj,rec=self.check_fit(token)
        except ValueError as exc:
            QMessageBox.information(self,"ICA 视觉复核",str(exc))
            return
        total=self.ica.n_components_
        selected,ok=QInputDialog.getText(self,"ICA 视觉复核",
            f"成分编号 0–{total-1}，用逗号分隔，或输入“全部”",
            text=", ".join(map(str,self.fit_auto)) or "0")
        if not ok: return
        try:
            picks=list(range(total)) if selected.strip()=="全部" else list(dict.fromkeys(int(n.strip()) for n in selected.replace("，",",").split(",")))
            if not picks or min(picks)<0 or max(picks)>=total: raise ValueError()
        except ValueError:
            QMessageBox.warning(self,"成分编号","请输入本次分解中的整数编号。")
            return
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from ..core.ica_visual import evidence
        folder=TemporaryDirectory(prefix="biap-ica-")
        raw,ica=self.raw_ica,self.ica
        def done(paths):
            self.check_fit(token)
            self.ctx.ai_action.setChecked(True)
            panel=self.ctx.assistant
            if panel: panel.add_ica_images(rec.id,token,picks,paths,folder,self.marked_components())
        self.run("生成 ICA 成分复核图",lambda:evidence(raw,ica,picks,Path(folder.name)),done)

    def _after(self, msg):
        self.project.save()
        self.ctx.project_changed(msg)

    def _clean_result(self, record, raw):
        partial = record.step_history if record.status != pj.REC_DONE else []
        title = partial[-1]["step"] + " · 单步结果" if partial else "预处理完成"
        result = analysis.Result("预处理", f"{record.id} · {title}")
        sfreq = raw.info["sfreq"]
        n_eeg = raw.get_channel_types().count("eeg")
        result.metrics = [
            ("脑电通道", str(n_eeg), f"保存的通道总数 {len(raw.ch_names)}"),
            ("时长", f"{raw.n_times / sfreq:.1f} 秒", f"采样率 {sfreq:g} Hz"),
        ]
        if partial:
            result.params["已运行步骤"] = " → ".join(item["step"] for item in partial)
            result.notes = [f"输入：{partial[-1]['input'] or record.source}", f"保存位置：{record.clean_file}"]
        history = record.processed_ica
        if history:
            removed = "、".join(f"IC-{i:03d}" for i in history["exclude"])
            result.metrics.append(("ICA 去除", removed or "无", ICA_MODES[history["mode"]]))
        elif not partial:
            result.metrics.append(("ICA 去除", "未记录", "历史结果未保存成分清单"))
        result.notes.append("功率谱显示处理后各频率的信号强度，可检查工频附近是否仍有突出峰值。"
                            "下一步可到「事件与分段」选择事件或固定时长，划分试次。")
        try:
            result.figures.append(("处理后功率谱", single.plot_psd(raw, 0, min(60, sfreq / 2))))
        except (ValueError, RuntimeError) as exc:
            result.notes.append(f"连续数据已保存。功率谱未能绘制：{exc}")
        return result

    def process_step(self, step, source=None):
        if step not in pj.SINGLE_STEPS:
            raise ValueError("未知的预处理步骤：" + str(step))
        cur = self._need_rec()
        if cur is None or self.ctx.runner.busy:
            return
        subj, rec = cur
        self.step_picker.setCurrentText(step)
        if source is not None:
            index = self.step_source.findData(source)
            if index < 0:
                raise ValueError("未知的单步输入来源。")
            self.step_source.setCurrentIndex(index)
        source = self.step_source.currentData()
        if not self.ctx.finish_mark_editors([rec.id]) or not self._save_pipe():
            return
        project = self.project
        def work():
            raw = pj.preprocess_step(project, subj, rec, step, source=source, log=log)
            try:
                return self._clean_result(rec, raw)
            finally:
                raw.close()
        def done(result):
            if self._still_selected(project, rec):
                self.raw = self.ica = self.raw_ica = self._sig = None
                self.component_maps.clear()
                self.work_tabs.setTabEnabled(1, False)
                self.result_view.show_result(result)
            if self.project is project:
                self.ctx.project_changed(f"{rec.id} · {step}已保存")
                if self._still_selected(project, rec):
                    self.work_tabs.setCurrentIndex(2)
        return self.run(f"{rec.id} · 只运行{step}", work, done)

    def process_one(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        if not self.ctx.finish_mark_editors([r.id]) or not self._save_pipe():
            return
        proj = self.project
        use = self.raw is not None and not self._raw_from_single_fit and self._sig == self._signature(s, r)
        raw = self.raw.copy() if use else None
        ica = self.ica if use and getattr(self,"fit_settings",None)==self.fit_controls() else None
        if self.raw is not None and not use:
            log("设置或坏导在载入后改过，按新设置重新读取处理")

        def work():
            out = pj.preprocess_recording(proj, s, r, raw=raw, ica=ica, log=log)
            return self._clean_result(r, out)

        def done(res):
            self._after(f"{r.id} 预处理完成")
            self.result_view.show_result(res)

        self.work_tabs.setCurrentIndex(0)
        self.steps.status.setText("正在处理")
        return self.run(f"处理 {r.id}", work, done)

    def process_all(self):
        if not self.need_project(need_done=False):
            return
        if not self.ctx.finish_mark_editors() or not self._save_pipe():
            return
        proj = self.project
        allr = proj.recordings()
        if not allr:
            QMessageBox.information(self, "没有原始数据", "项目里没有需要预处理的原始数据。")
            return
        todo = [(s, r) for s, r in allr if r.status != pj.REC_DONE]
        if not todo:
            if QMessageBox.question(self, "全部已处理", "所有记录都处理过了。用当前设置全部重新处理吗？") \
                    != QMessageBox.Yes:
                return
            todo = allr

        def work():
            rows, failed = [], []
            for i, (s, r) in enumerate(todo):
                log(f"—— 批量处理 {i + 1}/{len(todo)}：{r.id}")
                try:
                    out = pj.preprocess_recording(proj, s, r, log=log)
                    rows.append([s.name, r.condition or "—", str(len(out.ch_names)), ", ".join(r.bads) or "—",
                                 "、".join(f"IC-{i:03d}" for i in r.processed_ica["exclude"]) or "无"])
                except Exception as e:  # noqa: BLE001
                    failed.append(r.id)
                    rows.append([s.name, r.condition or "—", "失败", str(e)[:60], ""])
                    log(f"{r.id} 失败：{e}")
                proj.save()
            res = analysis.Result("预处理", f"批量预处理 · {len(todo)} 条记录")
            res.metrics = [("成功", str(len(todo) - len(failed)), ""), ("失败", str(len(failed)), ", ".join(failed))]
            res.table = (["被试", "条件", "脑电通道", "坏导", "ICA 去除"], rows)
            res.table_title = "各记录处理结果"
            res.notes = ["坏导只用了你标记过的。没标记的记录可以逐个点「自动检测坏导」后重新处理。"]
            return res

        def done(result):
            self.result_view.reset()
            self._after("批量预处理完成")
            self.batch_view.show_result(result)
            if self.work_tabs.indexOf(self.batch_view) < 0:
                self.work_tabs.addTab(self.batch_view, "批量汇总")
            self._batch_project = proj

        self.work_tabs.setCurrentIndex(0)
        self.steps.status.setText("批量处理中")
        return self.run("批量预处理", work, done)
