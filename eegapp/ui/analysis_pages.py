"""ERP、时频、解码、RSA 四个分析页。每页都可以选全组或单个被试，统计检验在设置里一起选。"""
import json
import os
from copy import copy, deepcopy

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QFileDialog, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QMessageBox, QSpinBox

from ..core import advanced, analysis, project as pj, stats
from ..core.analysis import GROUP
from ..core.single import parse_floats, channel_tokens, format_channels
from .base import BasePage, label_values, refresh_scope, scope_combo
from .channel_selection import ChannelPicker, channel_field
from .common import combo, fspin, ispin, line, log
from .widgets import CheckList, EmptyState, ResultView, btn, checkbox, hbox, label


def _pair(a, b):
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.setLayout(hbox(a, label("到", "Muted"), b, spacing=6))
    return w


class _AnalysisPage(BasePage):
    record_scope = None

    @property
    def project(self):
        project = super().project
        if self.record_scope or not project:
            return self.record_scope or project
        rid = self.selected_record_id()
        if not rid:
            return project
        try:
            _, rec = project.get_rec(rid)
        except KeyError:
            return project
        key = (project, rid, rec.record_epochs_file)
        if key != getattr(self, "_record_key", None):
            self._selected_project = pj.record_epoch_scope(project, rec)[0]
            self._record_key = key
        return self._selected_project

    def selected_record_id(self):
        if self.record_scope or not hasattr(self, "scope"):
            return None
        return self.scope.currentData(Qt.UserRole + 1)

    def select_record_scope(self, rid):
        subject, _ = self.ctx.project.get_rec(rid)
        index = (self.scope.findData(rid, Qt.UserRole + 1) if len(subject.recordings) > 1
                 else self.scope.findData(subject.name))
        if index >= 0:
            self.scope.setCurrentIndex(index)
        return index >= 0

    icon_name = "mdi6.chart-line"
    empty_title = ""
    empty_body = ""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.view = ResultView(EmptyState(self.icon_name, self.empty_title, self.empty_body,
            [btn("选择分析参数", lambda: self.show_settings(True), None, "mdi6.tune-variant")]))
        self.body.addWidget(self.view, 1)
        self._bound_project = None
        self._loading_settings = False

    def end_side(self):
        from .project_bridge import ANALYSIS, analysis_key, read_control
        self._keys = {attr: analysis_key(self.page_key, title) for title, attr in ANALYSIS[self.page_key].items()}
        self._defaults = {}
        for name, widget in vars(self).copy().items():
            if not isinstance(widget, (CheckList, QCheckBox, QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox)):
                continue
            if not self.groups.isAncestorOf(widget):
                continue
            if isinstance(widget, CheckList):
                signal = widget.itemChanged   # rebuilding the available choices blocks this signal
            else:
                self._defaults[name] = read_control(widget)
                if isinstance(widget, QComboBox):
                    signal = widget.currentTextChanged
                elif isinstance(widget, QCheckBox):
                    signal = widget.toggled
                elif isinstance(widget, QLineEdit):
                    signal = widget.textChanged
                else:
                    signal = widget.valueChanged
            signal.connect(lambda *_, key=name: self._save_control(key))

    def _preferences(self):
        project = self.ctx.project
        return project.assistant_data.get("analysis", {}).get(self.page_key, {}) if project else {}

    def _selection_key(self, name):
        scope = self.selected_record_id() or self.scope.currentData()
        return json.dumps([scope, self.label.currentText()]) if name == "classes" else scope

    def _save_control(self, name):
        from .project_bridge import read_control
        project = self.ctx.project
        if (not project or self._bound_project is not project or self._loading_settings or self.record_scope
                or self.ctx.stack.currentWidget() is not self):
            return
        widget = getattr(self, name)
        value = widget.checked() if isinstance(widget, CheckList) else read_control(widget)
        if name == "scope":
            value = {"record":self.selected_record_id()} if self.selected_record_id() else widget.currentData()
        if name == "do_stat":
            self._cluster_requested = value
        key = self._keys.get(name, name)
        scoped = name in ("conds", "label", "classes")
        staged = copy(project)
        staged.assistant_data = deepcopy(project.assistant_data)
        prefs = staged.assistant_data.setdefault("analysis", {}).setdefault(self.page_key, {})
        if scoped:
            if name == "conds" and self.selected_record_id():
                key = "record_conditions"
            elif name == "label" and self.selected_record_id():
                key = "record_labels"
            prefs = prefs.setdefault(key, {})
            key = self._selection_key(name)
        if key is None or prefs.get(key) == value:
            return
        prefs[key] = value
        try:
            staged.save()
        except (OSError, ValueError) as exc:
            self._bound_project = None
            self.on_show()
            QMessageBox.warning(self, "分析参数未保存", str(exc))
            return
        project.assistant_data, project._saved_bytes = staged.assistant_data, staged._saved_bytes
        self.ctx.statusBar().showMessage(f"已保存{self.page_key}分析设置。", 3500)

    def on_show(self):
        from .project_bridge import put_control
        super().on_show()
        self._loading_settings = True
        try:
            preferences = self._preferences()
            if self._bound_project is not self.ctx.project:
                self._bound_project = self.ctx.project
                for name, default in self._defaults.items():
                    if name in ("scope", "test", "label"):
                        continue
                    value = preferences.get(self._keys.get(name, name), default)
                    put_control(getattr(self, name), value)
                    if name == "do_stat":
                        self._cluster_requested = value
                for name in ("conds", "classes", "label", "test", "scope"):
                    widget = getattr(self, name, None)
                    if widget is not None:
                        with QSignalBlocker(widget):
                            widget.clear()
            previous_record = self.selected_record_id()
            refresh_scope(self.scope, self.record_scope or self.ctx.project)
            if self.ctx.project and not self.record_scope:
                with QSignalBlocker(self.scope):
                    for subject, rec in self.ctx.project.recordings():
                        if len(subject.recordings) > 1 and rec.record_epochs_file and os.path.isfile(self.ctx.project.path(rec.record_epochs_file)):
                            self.scope.addItem(f"单条记录 · {rec.id}", subject.name)
                            self.scope.setItemData(self.scope.count()-1, rec.id, Qt.UserRole+1)
                    if previous_record:
                        index = self.scope.findData(previous_record, Qt.UserRole+1)
                        if index >= 0:
                            self.scope.setCurrentIndex(index)
            if not self.record_scope and "scope" in preferences:
                saved = preferences["scope"]
                index = (self.scope.findData(saved.get("record"), Qt.UserRole+1) if isinstance(saved, dict)
                         else self.scope.findData(saved))
                if index >= 0:
                    with QSignalBlocker(self.scope):
                        self.scope.setCurrentIndex(index)
        finally:
            self._loading_settings = False

    def run_analysis(self, title, project, settings, work):
        scope = settings["scope"]
        subjects = [s for s in project.done_subjects() if scope == GROUP or s.name == scope]
        sources = tuple((s.name, s.epochs_file, tuple(r.id for r in s.recordings)) for s in subjects)
        key = (title, sources, json.dumps(settings, ensure_ascii=False, sort_keys=True))

        def show_result(res):
            res.report_key = key
            res.params.setdefault("范围", scope)
            if scope != GROUP and len(sources) == 1 and sources[0][2]:
                res.params["记录"] = "、".join(sources[0][2])
            self.view.show_result(res)
            self.ctx.add_report(res)

        return self.run(title, work, show_result)


class _ConditionPage(_AnalysisPage):
    max_conditions = None

    def select_channels(self):
        import mne
        if not self.need_project():
            return
        project, scope, conditions = self.project, self.scope.currentData(), self.conds.checked()
        before = self.ch.text()
        def read():
            subjects = analysis._subjects(project, scope, conditions, log=log)
            groups = [[epochs.ch_names[i] for i in mne.pick_types(epochs.info, eeg=True)] for _, epochs in subjects]
            shared = set.intersection(*(set(group) for group in groups))
            return groups[0], [name for name in groups[0] if name in shared]
        def show(result):
            def current():
                return (self.ctx.stack.currentWidget() is self and self.project is project and
                        self.scope.currentData() == scope and self.conds.checked() == conditions and self.ch.text() == before)
            if not current():
                return
            first, shared = result
            exact = {name.casefold():name for name in shared}
            selected = []
            for name, literal in channel_tokens(before):
                if not literal and name.isdigit() and 0 < int(name) <= len(first):
                    name = first[int(name)-1]
                if name.casefold() in exact:
                    selected.append(exact[name.casefold()])
            description = self.scope.currentText() + " · 勾选参与平均的 EEG 通道。"
            if scope == GROUP:
                description += "这里只显示参与分析的被试共有的通道。"
            description += "留空将由分析程序自动选择一个通道。"
            dialog = ChannelPicker(self, "选择分析通道", description, shared, ["eeg"] * len(shared), selected)
            if dialog.exec() and current():
                self.ch.setText(format_channels(dialog.selected_names()))
        return self.ctx.runner.run("读取分析范围内的 EEG 通道", read, show)

    def _add_condition_picker(self, section, caption, height):
        self.conds = section.row(caption, CheckList(height))
        self.selection_status = section.widget(label("", "Sub"))
        self.selection_status.setWordWrap(True)
        self.conds.changed.connect(self._selection_changed)

    def on_show(self):
        super().on_show()
        self._fill_conds()

    def _fill_conds(self):
        scope = self.scope.currentData()
        project = self.project
        counts = project.condition_counts(scope) if project else {}
        previous = self.conds.checked()
        keep_empty = (not previous and self.conds.count() > 0
                      and getattr(self, "_condition_scope", None) == (project, scope))
        self._condition_scope = (project, scope)
        record_id = self.selected_record_id()
        if self.record_scope and scope not in (None, GROUP):
            records = project.get(scope).recordings
            if len(records) == 1:
                record_id = records[0].id
        items, selected = self.condition_menu(counts, scope, record_id, previous, keep_empty)
        self._condition_counts = counts
        self.conds.set_items(items, checked=selected, default_all=False)

    def condition_menu(self, counts, scope, record_id=None, previous=(), keep_empty=False):
        """Use the same available checks in the window and in assistant workflow previews."""
        coverage, trials = {}, {}
        for row in counts.values():
            for name, n in row.items():
                coverage[name] = coverage.get(name, 0) + 1
                trials[name] = trials.get(name, 0) + n
        names = sorted(coverage, key=coverage.get, reverse=True)
        items = [(name, (f"{coverage[name]}/{len(counts)} 人 · " if scope == GROUP else "")
                  + f"{trials[name]} 个试次") for name in names]
        saved = self._preferences().get("conds", {}).get(scope)
        if record_id:
            subject = self.ctx.project.get_rec(record_id)[0]
            if len(subject.recordings) > 1:
                by_record = self._preferences().get("record_conditions", {})
                if record_id in by_record:
                    saved = by_record[record_id]
                elif saved and not any(name in coverage for name in saved):
                    saved = None
            # Selections left in another record's controls must not seed this run.
            previous, keep_empty = (), False
        selected = [name for name in (previous if saved is None else saved) if name in coverage]
        if names and not selected and not keep_empty and saved is None:
            first = names[0]
            selected = [first]
            # 第二个默认条件需在同一批被试中出现，且不能重复选入第一个条件下的试次。
            shared = set.intersection(*(set(row) for row in counts.values() if first in row))
            first_tags = set(first.split("/"))
            matching = [set(name.split("/")) for name in names if first_tags.issubset(name.split("/"))]
            for name in names[1:]:
                tags = set(name.split("/"))
                if name in shared and not any(tags <= event for event in matching):
                    selected.append(name)
                    break
        return items, selected

    def _participating_rows(self):
        selected = self.conds.checked()
        return [row for row in self._condition_counts.values() if all(c in row for c in selected)] if selected else []

    def _selection_changed(self):
        n = len(self.conds.checked())
        count = f"已选 {n} 个条件" if self.max_conditions is None else f"已选 {n} 个条件，最多 {self.max_conditions} 个"
        if not self.conds.count():
            reason = "当前范围没有保留下来的试次，请检查分段结果。"
        elif not n:
            reason = "请勾选至少 1 个条件。"
        elif self.max_conditions and n > self.max_conditions:
            reason = f"请取消勾选 {n - self.max_conditions} 个条件。"
        elif not self._participating_rows():
            reason = "没有被试同时保留这些条件，请调整范围或勾选项。"
        else:
            reason = ""
        self.selection_status.setText(f"{count}。{reason}" if reason else count)
        self.run_btn.setEnabled(not reason)
        self.run_btn.setToolTip(reason)

    def _stat_level(self):
        rows = self._participating_rows()
        if len(rows) >= 2:
            return "group"
        if rows and all(rows[0][c] >= 2 for c in self.conds.checked()):
            return "trial"
        return None


# ====================================================================== ERP
class ERPPage(_ConditionPage):
    page_key = "ERP"
    title = "ERP 事件相关电位"
    subtitle = "叠加平均、条件比较、逐时间点统计、地形图，一次生成"
    icon_name = "mdi6.sine-wave"
    empty_title = "看看大脑对刺激的反应"
    empty_body = "在参数设置中选好条件和通道，点「运行 ERP 分析」。选「全组」时以被试为单位做统计，选单个被试时以试次为单位。"

    def __init__(self, ctx):
        super().__init__(ctx)
        s = self.section("数据")
        self.scope = s.row("范围", scope_combo())
        self._add_condition_picker(s, "条件", 120)
        s = self.section("通道与时间")
        self.ch = channel_field(s, "通道", line("Pz, Cz", "名字或序号，多个取平均"), "选择通道…", self.select_channels)
        self.w0, self.w1 = fspin(-0.2, -60, 60, 0.1, 3, " s"), fspin(0.8, -60, 60, 0.1, 3, " s")
        s.row("显示", _pair(self.w0, self.w1))
        self.m0, self.m1 = fspin(0.3, -60, 60, 0.05, 3, " s"), fspin(0.5, -60, 60, 0.05, 3, " s")
        s.row("指标时间窗", _pair(self.m0, self.m1))
        self.topo = s.row("地形图时刻", line("auto", "auto = 自动找峰值；或 0.1, 0.4"))
        s = self.section("统计检验")
        self.test = s.row("方法", combo([]))
        self.corr = s.row("多重比较", combo(stats.CORRECTIONS, "FDR"))
        self.alpha = s.row("p 阈值", fspin(0.05, 0.0001, 0.5, 0.01, 3))
        self.niter = s.row("置换次数", ispin(1000, 50, 100000, 100))
        self.levels = s.row("两因素水平", line("2, 2", "条件按 A1B1、A1B2、A2B1、A2B2 排"))
        self.end_side()
        self.run_btn = btn("运行 ERP 分析", self.go, "primary", "mdi6.play")
        self.action_row(self.run_btn)
        self.scope.currentIndexChanged.connect(self._fill_conds)
        self.conds.changed.connect(self._tests)

    def _tests(self):
        with QSignalBlocker(self.test):
            self._fill_tests()

    def _fill_tests(self):
        k = len(self.conds.checked())
        level = self._stat_level()
        cur = self._preferences().get("test", self.test.currentText())
        keep_none = cur == "不做检验" and ("test" in self._preferences() or self.test.count() > 1)
        self.test.clear()
        self.test.addItem("不做检验")
        items = stats.tests_for(k, level) if level else []
        self.test.addItems(items)
        self.test.setEnabled(bool(items))
        self.test.setToolTip("" if items else "所选条件的有效样本不足，仅展示描述性结果。")
        if cur in items or keep_none:
            self.test.setCurrentText(cur)
        elif items:
            pref = [x for x in items if x.startswith("cluster")]
            self.test.setCurrentText(pref[0] if pref else items[0])

    def go(self):
        if not self.need_project() or not self.run_btn.isEnabled():
            return
        args = dict(scope=self.scope.currentData(), conditions=self.conds.checked(), channels=self.ch.text(),
                    win=(self.w0.value(), self.w1.value()), metric_win=(self.m0.value(), self.m1.value()),
                    test=self.test.currentText(), correction=self.corr.currentText(), alpha=self.alpha.value(),
                    n_iter=self.niter.value(), cluster_p=self.alpha.value(),
                    factor_levels=[int(x) for x in parse_floats(self.levels.text())] or None,
                    topo_times=self.topo.text().strip())
        p = self.project
        return self.run_analysis("ERP 分析", p, args, lambda: analysis.erp(p, log=log, **args))


# ====================================================================== 时频
class TFRPage(_ConditionPage):
    page_key = "时频"
    max_conditions = 2
    title = "时频与频谱"
    subtitle = "Morlet 小波时频图、cluster 置换检验、频段功率地形图"
    icon_name = "mdi6.chart-bell-curve-cumulative"
    empty_title = "节律的起伏，一目了然"
    empty_body = "选 1 个或 2 个条件和感兴趣的通道（例如枕区 O1, Oz, O2 看 alpha），点「运行时频分析」。"

    def __init__(self, ctx):
        super().__init__(ctx)
        s = self.section("数据")
        self.scope = s.row("范围", scope_combo())
        self._add_condition_picker(s, "条件（1–2 个）", 110)
        self.ch = channel_field(s, "通道", line("O1, Oz, O2", "多个取平均"), "选择通道…", self.select_channels)
        s = self.section("频率与时间")
        self.f0, self.f1 = fspin(4, 0.5, 500, 1, 1, " Hz"), fspin(30, 1, 1000, 1, 1, " Hz")
        s.row("频率", _pair(self.f0, self.f1))
        self.fs = s.row("频率步长", fspin(2, 0.1, 50, 0.5, 1, " Hz"))
        self.div = s.row("n_cycles = 频率 /", fspin(2, 0.1, 50, 0.5, 1))
        self.b0, self.b1 = fspin(-0.4, -60, 60, 0.05, 3, " s"), fspin(-0.1, -60, 60, 0.05, 3, " s")
        s.row("基线", _pair(self.b0, self.b1))
        self.w0, self.w1 = fspin(-0.2, -60, 60, 0.1, 3, " s"), fspin(0.8, -60, 60, 0.1, 3, " s")
        s.row("显示", _pair(self.w0, self.w1))
        self.decim = s.row("降采样倍数", ispin(2, 1, 20))
        s = self.section("指标与统计")
        self.mb0, self.mb1 = fspin(8, 0.5, 500, 1, 1, " Hz"), fspin(12, 0.5, 500, 1, 1, " Hz")
        s.row("指标频段", _pair(self.mb0, self.mb1))
        self.mt0, self.mt1 = fspin(0.2, -60, 60, 0.05, 3, " s"), fspin(0.6, -60, 60, 0.05, 3, " s")
        s.row("指标时间窗", _pair(self.mt0, self.mt1))
        self.do_stat = s.widget(checkbox("cluster 置换检验", True))
        self._cluster_requested = True
        self.do_stat.clicked.connect(self._remember_cluster)
        self.p = s.row("p 阈值", fspin(0.05, 0.0001, 0.5, 0.01, 3))
        self.niter = s.row("置换次数", ispin(500, 50, 100000, 100))
        self.end_side()
        self.run_btn = btn("运行时频分析", self.go, "primary", "mdi6.play")
        self.action_row(self.run_btn)
        self.scope.currentIndexChanged.connect(self._fill_conds)
        self.conds.changed.connect(self._stats_changed)

    def _remember_cluster(self, checked):
        self._cluster_requested = checked

    def _stats_changed(self):
        available = self._stat_level() is not None
        with QSignalBlocker(self.do_stat):
            self.do_stat.setChecked(available and self._cluster_requested)
        self.do_stat.setEnabled(available)
        self.do_stat.setToolTip("" if available else "所选条件的有效样本不足，仅展示描述性结果。")

    def go(self):
        if not self.need_project() or not self.run_btn.isEnabled():
            return
        p = self.project
        a = dict(scope=self.scope.currentData(), conditions=self.conds.checked(), channels=self.ch.text(),
                 fmin=self.f0.value(), fmax=self.f1.value(), fstep=self.fs.value(), cycles_div=self.div.value(),
                 baseline=(self.b0.value(), self.b1.value()), win=(self.w0.value(), self.w1.value()),
                 metric_band=(self.mb0.value(), self.mb1.value()), metric_win=(self.mt0.value(), self.mt1.value()),
                 do_stats=self.do_stat.isChecked(), p=self.p.value(), cluster_p=self.p.value(),
                 n_iter=self.niter.value(), decim=self.decim.value())
        return self.run_analysis("时频分析", p, a, lambda: analysis.tfr(p, log=log, **a))


# ====================================================================== 解码 / RSA 共用的数据选择
class _LabelPage(_AnalysisPage):
    def _data_section(self):
        s = self.section("数据与标签")
        self.scope = s.row("范围", scope_combo())
        self.label = s.row("标签", combo([]))
        self.classes = s.row("类别", CheckList(140))
        self.label.currentTextChanged.connect(self._classes)
        self.scope.currentIndexChanged.connect(self._scope_changed)

    def _stats_section(self, stats_t=(0.0, 1.0)):
        s = self.group_statistics = self.section("组水平统计检验")
        self.cluster = s.widget(checkbox("cluster 置换检验（否则逐点 t 检验）", True))
        self.p = s.row("p 阈值", fspin(0.05, 0.0001, 0.5, 0.01, 3))
        self.niter = s.row("置换次数", ispin(1000, 50, 100000, 100))
        self.s0, self.s1 = fspin(stats_t[0], -60, 60, 0.1, 3, " s"), fspin(stats_t[1], -60, 60, 0.1, 3, " s")
        s.row("检验时间段", _pair(self.s0, self.s1))

    def on_show(self):
        super().on_show()
        self._scope_changed()

    def _scope_changed(self):
        p = self.project
        scope = self.scope.currentData()
        self.group_statistics.setEnabled(scope == GROUP)
        if p and p.done_subjects():
            rid = self.selected_record_id()
            cur = self._preferences().get("record_labels" if rid else "label", {}).get(rid or scope, self.label.currentText())
            self.label.blockSignals(True)
            self.label.clear()
            cols = p.label_columns(scope)
            self.label.addItems(cols)
            if cur in cols:
                self.label.setCurrentText(cur)
            elif len(cols) > 1:
                self.label.setCurrentIndex(1)
            self.label.blockSignals(False)
            self._classes()
        else:
            with QSignalBlocker(self.label):
                self.label.clear()
            self.classes.set_items([])

    def _classes(self):
        p = self.project
        if p and p.done_subjects() and self.label.currentText():
            names = label_values(p, self.label.currentText(), self.scope.currentData())
            selected = self._preferences().get("classes", {}).get(self._selection_key("classes"))
            self.classes.set_items(names, checked=names if selected is None else selected, default_all=False)

    def _data_settings(self):
        return dict(scope=self.scope.currentData(), label=self.label.currentText(), classes=self.classes.checked())


class DecodePage(_LabelPage):
    page_key = "解码"
    title = "解码与反向编码"
    subtitle = "逐时间点 SVM 解码、跨时间泛化、IEM 特征重建"
    icon_name = "mdi6.brain"
    empty_title = "大脑在什么时候“知道”了答案"
    empty_body = "选择标签（条件或导入时附带的标签，如朝向）和要区分的类别，点「运行解码」。至少需要 2 类。"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._data_section()
        s = self.section("方法与参数")
        self.method = s.row("方法", combo(["逐时间点解码", "跨时间泛化", "反向编码模型 IEM"]))
        self.navg = s.row("每次平均试次 navg", ispin(5, 1, 200))
        self.win = s.row("时间窗（采样点）", ispin(5, 1, 500))
        self.step = s.row("步长（采样点）", ispin(5, 1, 500))
        self.folds = s.row("交叉验证折数", ispin(3, 2, 20))
        self.reps = s.row("重复次数", ispin(5, 1, 100))
        self.circ = s.widget(checkbox("IEM：特征是环形的（朝向、角度）", True))
        self.nperm = s.row("IEM 置换次数", ispin(2000, 100, 100000, 500))
        self.jobs = s.row("并行线程", ispin(1, 1, max(1, os.cpu_count() or 1)))
        self._stats_section()
        self.end_side()
        self.action_row(btn("运行解码", self.go, "primary", "mdi6.play"))

    def go(self):
        if not self.need_project():
            return
        if len(self.classes.checked()) < 2:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "类别", "至少选择 2 个类别。")
            return
        project, data = self.project, self._data_settings()
        a = dict(method=self.method.currentText(), navg=self.navg.value(), time_win=self.win.value(),
                 time_step=self.step.value(), nfolds=self.folds.value(), nrepeats=self.reps.value(),
                 use_cluster=self.cluster.isChecked(), p=self.p.value(), cluster_p=self.p.value(),
                 n_iter=self.niter.value(), stats_time=(self.s0.value(), self.s1.value()),
                 circular=self.circ.isChecked(), num_perm=self.nperm.value(), n_jobs=self.jobs.value())
        return self.run_analysis(a["method"], project, {**data, **a},
                                 lambda: analysis.decode(analysis.decoding_data(project, **data, log=log), log=log, **a))


class RSAPage(_LabelPage):
    page_key = "RSA"
    title = "表征相似性分析"
    subtitle = "脑电 RDM 与假设模型逐时间比较"
    icon_name = "mdi6.dots-grid"
    empty_title = "大脑怎样组织这些类别"
    empty_body = "选择标签和类别，再选一个假设模型（例如朝向用环形距离），点「运行 RSA」。"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._data_section()
        s = self.section("模型与参数")
        self.kind = s.row("模型 RDM", combo(["环形距离（如朝向、位置）", "线性距离", "类别是否相同（0/1）", "从文件读取"]))
        self.mfile = s.row("模型文件", line("", "n×n 矩阵 .npy / .txt / .csv"))
        s.widget(btn("选择模型文件…", self._pick, "ghost", "mdi6.folder-open-outline"))
        self.win = s.row("时间窗（采样点）", ispin(5, 1, 500))
        self.step = s.row("步长（采样点）", ispin(5, 1, 500))
        self.dist = s.row("RDM 距离", combo(list(advanced.RDM_METHODS)))
        self._stats_section()
        self.end_side()
        self.action_row(btn("运行 RSA", self.go, "primary", "mdi6.play"))

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "模型 RDM", "", "矩阵 (*.npy *.txt *.csv);;所有文件 (*)")
        if p:
            self.mfile.setText(p)
            self.kind.setCurrentText("从文件读取")

    def go(self):
        if not self.need_project():
            return
        project, data = self.project, self._data_settings()
        a = dict(model_kind=self.kind.currentText(), model_file=self.mfile.text().strip(), time_win=self.win.value(),
                 time_step=self.step.value(), distance=self.dist.currentText(), use_cluster=self.cluster.isChecked(),
                 p=self.p.value(), cluster_p=self.p.value(), n_iter=self.niter.value(),
                 stats_time=(self.s0.value(), self.s1.value()))
        return self.run_analysis("RSA", project, {**data, **a},
                                 lambda: analysis.rsa(analysis.decoding_data(project, **data, log=log), log=log, **a))
