"""事件与分段页：给每条记录添加、整理事件，然后分段。

事件和数据分开保存，任何时候都可以改；改完重新分段即可。没有事件也可以按固定长度分段。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QListWidgetItem, QMessageBox, QPlainTextEdit, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import analysis, events as ev, project as pj, reading
from .. import theme
from ..theme import C
from .base import BasePage
from .common import combo, fspin, ispin, line, log
from .widgets import (Card, CheckList, FigureCard, MetricGrid, btn, checkbox, hbox, icon, label, side_list)

NONE = "（不用）"


class EventsPage(BasePage):
    title = "事件与分段"
    subtitle = "事件和数据分开保存，随时可以添加、改名；没有事件也能按固定长度分段"

    def __init__(self, ctx):
        super().__init__(ctx)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.recs = side_list(210)
        self.recs.currentRowChanged.connect(self._select)
        row.addWidget(self.recs)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        cl = QVBoxLayout(inner)
        cl.setContentsMargins(0, 0, 8, 8)
        cl.setSpacing(12)
        self.head = label("—", "H2")
        cl.addWidget(self.head)
        self.metrics = MetricGrid(3)
        cl.addWidget(self.metrics)
        kinds = Card("事件种类", "选中一行或几行，可以在右边平移或删除；名字以 BAD_ 开头的表示坏段，分段时跳过")
        self.kinds = QTableWidget(0, 3)
        self.kinds.setHorizontalHeaderLabels(["事件", "个数", "第一次出现（秒）"])
        self.kinds.verticalHeader().setVisible(False)
        self.kinds.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.kinds.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.kinds.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.kinds.setMinimumHeight(150)
        kinds.add(self.kinds)
        cl.addWidget(kinds)
        self.fig_slot = QVBoxLayout()
        cl.addLayout(self.fig_slot)
        self.empty = label("这条记录还没有事件。用右边“添加事件”里的任意一种方式添加；"
                           "只做静息态或频谱分析的话，也可以不加事件，分段方式选“固定长度”。", "Sub", wrap=True)
        cl.addWidget(self.empty)
        cl.addStretch(1)
        sc.setWidget(inner)
        row.addWidget(sc, 1)
        self.body.addLayout(row, 1)
        self._fig = None
        self._headers = {}
        self._last_table = None      # (示例数据, 示例表格, 导入方式)

        s = self.section("添加事件")
        for text, slot, ic in [("从表格导入…", self.import_table, "mdi6.table-arrow-left"),
                               ("从通道读取…", self.from_channel, "mdi6.sine-wave"),
                               ("按固定间隔生成…", self.fixed, "mdi6.ray-vertex"),
                               ("手动输入时间…", self.manual, "mdi6.keyboard-outline"),
                               ("在波形上标记…", self.mark_on_wave, "mdi6.cursor-default-click-outline")]:
            s.widget(btn(text, slot, "accent", ic))
        s.widget(btn("恢复成数据自带的事件", self.reset_events, "ghost", "mdi6.restore"))

        s = self.section("整理")
        self.rules = s.row("改名规则", QPlainTextEdit())
        self.rules.setPlaceholderText("每行一条：旧=新\n9xx=刺激出现（x 代表任意一位数字）\n1=睁眼\nS*=刺激")
        self.rules.setFixedHeight(92)
        s.widget(hbox(btn("改名", self.rename, None, "mdi6.rename-outline"),
                      btn("用到所有记录", lambda: self.rename(all_recs=True), "ghost")))
        self.shift_s = s.row("平移所选种类", fspin(0, -100, 100, 0.01, 3, " s"))
        s.widget(hbox(btn("平移", self.shift, None, "mdi6.arrow-left-right"),
                      btn("删除所选种类", self.delete, "danger", "mdi6.delete-outline")))

        s = self.section("分段")
        self.mode = s.row("分段方式", combo([pj.EPOCH_EVENTS, pj.EPOCH_FIXED]))
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.ev_list = s.row("用哪些事件", CheckList(140))
        self.tmin, self.tmax = fspin(-0.2, -60, 60, 0.1, 3, " s"), fspin(0.8, -60, 60, 0.1, 3, " s")
        self.t_row = s.row("时间窗（相对事件）", _pair(self.tmin, self.tmax))
        self.bl_on = s.widget(checkbox("基线校正", True))
        self.b0, self.b1 = fspin(-0.2, -60, 60, 0.05, 3, " s"), fspin(0, -60, 60, 0.05, 3, " s")
        self.bl_row = s.row("基线", _pair(self.b0, self.b1))
        self.flen = s.row("每段长度", fspin(2, 0.1, 600, 0.5, 2, " s"))
        self.fov = s.row("相邻段重叠", fspin(0, 0, 600, 0.5, 2, " s"))
        self.rej = s.row("振幅剔除（峰峰值）", fspin(150, 0, 1e5, 10, 0, " µV"))
        s.widget(label("0 表示不剔除。剔除太多时，结果里会列出“剔除最多的通道”，通常把它标成坏导更好。", "Muted", wrap=True))
        self.end_side()
        self.action_row(btn("分段这个被试", self.epoch_one, "primary", "mdi6.content-cut"),
                        btn("批量分段全部", self.epoch_all, None, "mdi6.lightning-bolt-outline"))
        self._mode_changed(self.mode.currentText())

    # ------------------------------------------------------------ 设置
    def _mode_changed(self, m):
        fixed = m == pj.EPOCH_FIXED
        for w in (self.ev_list, self.tmin, self.tmax, self.bl_on, self.b0, self.b1):
            w.setEnabled(not fixed)
        for w in (self.flen, self.fov):
            w.setEnabled(fixed)

    def _load_pipe(self):
        p = self.project.pipeline
        self.mode.setCurrentText(p.epoch_mode)
        self.tmin.setValue(p.tmin)
        self.tmax.setValue(p.tmax)
        self.bl_on.setChecked(p.baseline)
        self.b0.setValue(p.bl0)
        self.b1.setValue(p.bl1)
        self.flen.setValue(p.fixed_len)
        self.fov.setValue(p.fixed_overlap)
        self.rej.setValue(p.reject_uv)

    def _save_pipe(self):
        p = self.project.pipeline
        p.epoch_mode = self.mode.currentText()
        p.tmin, p.tmax, p.baseline = self.tmin.value(), self.tmax.value(), self.bl_on.isChecked()
        p.bl0, p.bl1 = self.b0.value(), self.b1.value()
        p.fixed_len, p.fixed_overlap, p.reject_uv = self.flen.value(), self.fov.value(), self.rej.value()
        if self.ev_list.count():
            all_ev = self.ev_list.names()
            sel = self.ev_list.checked()
            p.events = [] if set(sel) == set(all_ev) else sel
        self.project.save()

    def _refresh_event_choices(self):
        """所有记录里出现过的事件名（不含坏段）。"""
        p = self.project
        names = []
        for _, r in p.recordings():
            path = p.events_path(r)
            if os.path.exists(path):
                names += ev.names(ev.load(path))
        names = list(dict.fromkeys(names))
        chosen = p.pipeline.events
        self.ev_list.set_items(names, checked=[n for n in chosen if n in names] or names)

    # ------------------------------------------------------------ 显示
    def _current(self):
        it = self.recs.currentItem()
        if it is None or self.project is None:
            return None
        try:
            return self.project.get_rec(it.data(Qt.UserRole))
        except KeyError:
            return None

    def on_show(self, subject=None):
        super().on_show()
        p = self.project
        cur = subject or (self.recs.currentItem().data(Qt.UserRole) if self.recs.currentItem() else None)
        self.recs.blockSignals(True)
        self.recs.clear()
        if p is not None:
            self._load_pipe()
            for s, r in p.recordings():
                path = p.events_path(r)
                n = len(ev.load(path)) if os.path.exists(path) else None
                it = QListWidgetItem(icon("mdi6.map-marker-check-outline" if n else "mdi6.map-marker-outline",
                                          C["ok"] if n else C["muted"]),
                                     f"{s.name}" + (f"  ·  {r.condition}" if r.condition else "")
                                     + (f"  （{n}）" if n else ""))
                it.setData(Qt.UserRole, r.id)
                it.setToolTip(r.source)
                self.recs.addItem(it)
                if r.id == cur:
                    self.recs.setCurrentItem(it)
            if self.recs.currentRow() < 0 and self.recs.count():
                self.recs.setCurrentRow(0)
            self._refresh_event_choices()
        self.recs.blockSignals(False)
        self._select()

    def _header(self, r):
        """记录的文件头（采样率、时长、开始时间、通道名），缓存起来。"""
        if r.id not in self._headers:
            raw = reading.read_header(r.source, log=log)
            md = raw.info["meas_date"]
            self._headers[r.id] = dict(sfreq=raw.info["sfreq"], dur=float(raw.times[-1]),
                                       start=md.timestamp() + raw.first_time if md else None,
                                       ch_names=list(raw.ch_names))
        return self._headers[r.id]

    def _select(self, *_):
        cur = self._current()
        if cur is None:
            self.head.setText("项目里还没有原始数据，先到「项目」页导入")
            self.metrics.set([])
            self.kinds.setRowCount(0)
            self._set_fig(None)
            return
        s, r = cur
        try:
            df = self.project.events(r, log=log)
            h = self._header(r)
        except Exception as e:  # noqa: BLE001
            self.head.setText(f"{r.id}：读取失败 {e}")
            return
        self.head.setText(f"{s.name}" + (f" · {r.condition}" if r.condition else "") +
                          f"　　{h['dur']:.0f} 秒 · {h['sfreq']:g} Hz")
        items = ev.summary(df)
        self.metrics.set([("事件", str(len(df)), f"{len([1 for n, _, _ in items if not ev.is_bad(n)])} 种"),
                          ("坏段", str(int(df['description'].map(ev.is_bad).sum())) if len(df) else "0",
                           "名字以 BAD_ 开头"),
                          ("预处理", r.status, "分段要用预处理后的数据")])
        self.kinds.setRowCount(len(items))
        for i, (n, c, t0) in enumerate(items):
            for j, v in enumerate([n, str(c), f"{t0:.2f}"]):
                it = QTableWidgetItem(v)
                if j == 0 and ev.is_bad(n):
                    it.setForeground(QColor(C["danger"]))
                self.kinds.setItem(i, j, it)
        self.kinds.resizeColumnsToContents()
        self.kinds.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.kinds.setFixedHeight(min(40 + 32 * max(len(items), 1), 300))
        self.empty.setVisible(not len(df))
        self._set_fig(ev.plot_timeline(df, h["dur"], "事件在记录中的位置") if len(df) else None)

    def _set_fig(self, fig):
        if self._fig is not None:
            self._fig.setParent(None)
            self._fig.deleteLater()
            self._fig = None
        if fig is not None:
            self._fig = FigureCard("时间线", fig)
            self.fig_slot.addWidget(self._fig)

    def _selected_kinds(self):
        rows = sorted({i.row() for i in self.kinds.selectedIndexes()})
        return [self.kinds.item(r, 0).text() for r in rows]

    def _need_rec(self):
        if not self.need_project(need_done=False):
            return None
        cur = self._current()
        if cur is None:
            QMessageBox.information(self, "没有原始数据", "请先在「项目」页导入原始数据。")
        return cur

    def _store(self, r, df, msg, replace=False):
        old = self.project.events(r, log=log)
        new = ev.tidy(df) if replace else ev.merge(old, df)
        self.project.save_events(r, new)
        log(f"{r.id}：{msg}（现在共 {len(new)} 个事件）")
        self._after_change()

    def _after_change(self):
        self.project.save()
        self.on_show()

    # ------------------------------------------------------------ 添加
    def import_table(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        h = self._header(r)
        guess = None
        if self._last_table:
            guess = ev.find_partner_table(r.source, self._last_table[0], self._last_table[1])
        guess = guess or ev.find_partner_table(r.source)
        spec0 = (self._last_table[2] if self._last_table else None) or self.project.pipeline.event_import or None
        d = TableImportDialog(self, r, h, guess, spec0, len(self.project.recordings()))
        if not d.exec():
            return
        df, spec, path = d.result_events, d.spec(), d.path
        self._last_table = (r.source, path, spec)
        self.project.pipeline.event_import = dict(spec, example_data=r.source, example_table=path)
        self._store(r, df, f"从表格导入 {len(df)} 个事件", replace=d.replace.isChecked())
        if d.apply_all.isChecked():
            self._import_all(spec, r.source, path, d.replace.isChecked())

    def _import_all(self, spec, ex_data, ex_table, replace):
        p = self.project
        targets = [(s, r) for s, r in p.recordings() if r.source != ex_data]

        def work():
            rows = []
            for s, r in targets:
                t = ev.find_partner_table(r.source, ex_data, ex_table)
                if not t:
                    rows.append([r.id, "没找到表格", ""])
                    continue
                try:
                    h = self._header(r)
                    df = ev.table_to_events(ev.read_table(t, int(spec.get("skip", 0))), spec, h["sfreq"],
                                            h["start"], h["dur"], log=log)
                    old = p.events(r, log=log)
                    new = ev.tidy(df) if replace else ev.merge(old, df)
                    p.save_events(r, new)
                    rows.append([r.id, str(len(df)), os.path.basename(t)])
                except Exception as e:  # noqa: BLE001
                    rows.append([r.id, "失败", str(e)[:60]])
            return rows

        def done(rows):
            res = analysis.Result("事件", "批量导入事件")
            res.table = (["记录", "导入个数", "表格文件"], rows)
            res.table_title = "各记录导入结果"
            ok = sum(1 for x in rows if x[1].isdigit())
            QMessageBox.information(self, "批量导入", f"{ok}/{len(rows)} 条记录导入成功。" +
                                    ("\n\n没成功的：\n" + "\n".join(f"{a}：{b} {c}" for a, b, c in rows
                                                               if not b.isdigit()) if ok < len(rows) else ""))
            self._after_change()

        self.run("批量导入事件", work, done)

    def from_channel(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        h = self._header(r)
        d = ChannelEventDialog(self, h["ch_names"])
        if not d.exec():
            return
        ch, prefix = d.channel(), d.prefix.text().strip()

        def work():
            raw = reading.read_header(r.source, log=log)
            v = raw.get_data(picks=[ch])[0]
            df = ev.from_channel(v * _unit_scale(raw, ch), raw.info["sfreq"])
            if prefix:
                df["description"] = prefix + df["description"]
            return df

        self.run(f"从 {ch} 读取事件",
                 work, lambda df: self._store(r, df, f"从通道 {ch} 读取 {len(df)} 个事件", d.replace.isChecked())
                 if len(df) else QMessageBox.information(self, "没有事件", f"{ch} 里没有找到非 0 的数值变化。"))

    def fixed(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        h = self._header(r)
        d = QDialog(self)
        d.setWindowTitle("按固定间隔生成事件")
        f = QFormLayout(d)
        a, b = fspin(0, 0, 1e6, 1, 2, " s"), fspin(round(h["dur"], 2), 0, 1e6, 1, 2, " s")
        step = fspin(2, 0.01, 1e5, 0.5, 3, " s")
        name = line("片段", "")
        f.addRow(label("在一段时间里每隔一段时间打一个事件，适合静息态，或给“睁眼/闭眼”这样的长时段切小段。",
                       "Sub", wrap=True))
        f.addRow("从", a)
        f.addRow("到", b)
        f.addRow("每隔", step)
        f.addRow("事件名", name)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec():
            try:
                df = ev.fixed_interval(a.value(), b.value(), step.value(), name.text().strip() or "片段")
            except ValueError as e:
                QMessageBox.warning(self, "参数", str(e))
                return
            self._store(r, df, f"按 {step.value():g} 秒间隔生成 {len(df)} 个事件")

    def manual(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        d = QDialog(self)
        d.setWindowTitle("手动输入事件")
        d.resize(460, 380)
        lay = QVBoxLayout(d)
        lay.addWidget(label("每行一个事件：时间（秒，从记录开头算）和事件名，例如“12.5 刺激”。"
                            "也可以一行写多个时间，名字统一用下面的默认名。", "Sub", wrap=True))
        txt = QPlainTextEdit()
        txt.setPlaceholderText("12.5 刺激\n30 反应\n45.2, 刺激")
        lay.addWidget(txt, 1)
        dn = line("事件", "")
        lay.addLayout(hbox(label("默认名", "Sub"), dn))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if d.exec():
            try:
                df = ev.parse_manual(txt.toPlainText(), dn.text().strip() or "事件")
            except ValueError as e:
                QMessageBox.warning(self, "格式", str(e))
                return
            self._store(r, df, f"手动添加 {len(df)} 个事件")

    def mark_on_wave(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        p = self.project

        def work():
            if r.status == pj.REC_DONE and r.clean_file and os.path.exists(p.path(r.clean_file)):
                import mne
                raw = mne.io.read_raw_fif(p.path(r.clean_file), preload=True, verbose=False)
            else:
                raw, _ = pj.open_raw(s, r, p.pipeline, log=log)
            raw.set_annotations(ev.to_annotations(p.events(r, log=log)))
            return raw

        def done(raw):
            pre = r.status == pj.REC_DONE
            try:
                b = raw.plot(duration=20, n_channels=min(24, len(raw.ch_names)), block=False, show=True,
                             theme=theme.BROWSER, highpass=None if pre else 0.5, lowpass=None if pre else 40,
                             title=f"{r.id}：按 A 进入标记模式，拖动添加事件；关闭窗口后保存")
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "波形浏览器", str(e))
                return

            def closed():
                df = ev.from_raw(raw)
                p.save_events(r, df)
                log(f"{r.id}：波形标记已保存，现在共 {len(df)} 个事件")
                self._after_change()
            self.ctx.keep_window(b, closed)
            QMessageBox.information(self, "在波形上标记",
                                    "在打开的波形窗口里：\n\n· 按 A 键进入标记模式\n· 在上方输入新事件名并添加\n"
                                    "· 在波形上拖动，就会在那个位置加一个事件（拖出的宽度就是时长）\n"
                                    "· 选中已有事件按 Delete 删除\n\n关闭波形窗口后自动保存。"
                                    "坏段可以起名 BAD_xxx，分段时会跳过。")

        self.run(f"载入 {r.id} 的波形", work, done)

    def reset_events(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        if QMessageBox.question(self, "恢复", f"清除 {r.id} 现有的事件，恢复成数据文件自带的事件？") != QMessageBox.Yes:
            return
        path = self.project.events_path(r)
        if os.path.exists(path):
            os.remove(path)
        self.project.events(r, log=log)
        self._after_change()

    # ------------------------------------------------------------ 整理
    def rename(self, all_recs=False):
        if not self.need_project(need_done=False):
            return
        try:
            rules = ev.parse_rules(self.rules.toPlainText())
        except ValueError as e:
            QMessageBox.warning(self, "改名规则", str(e))
            return
        if not rules:
            QMessageBox.information(self, "改名规则", "先在上面写改名规则，例如 9xx=刺激出现")
            return
        cur = self._current()
        targets = self.project.recordings() if all_recs else ([cur] if cur else [])
        total = {}
        for _, r in targets:
            df, changed = ev.apply_rules(self.project.events(r, log=log), rules)
            self.project.save_events(r, df)
            total.update(changed)
        log(f"改名：{len(total)} 种事件名被改动" + (f"（{len(targets)} 条记录）" if all_recs else ""))
        self._after_change()

    def shift(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        which = self._selected_kinds()
        if not which:
            QMessageBox.information(self, "平移", "先在“事件种类”表里选中要平移的事件。")
            return
        sec = self.shift_s.value()
        self.project.save_events(r, ev.shift(self.project.events(r), which, sec))
        log(f"{r.id}：{', '.join(which)} 平移 {sec:+g} 秒")
        self._after_change()

    def delete(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        which = self._selected_kinds()
        if not which:
            QMessageBox.information(self, "删除", "先在“事件种类”表里选中要删除的事件。")
            return
        if QMessageBox.question(self, "删除事件", f"删除 {r.id} 里的 {', '.join(which)}？") != QMessageBox.Yes:
            return
        self.project.save_events(r, ev.drop(self.project.events(r), which))
        self._after_change()

    # ------------------------------------------------------------ 分段
    def epoch_one(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, _ = cur
        self._save_pipe()
        p = self.project

        def work():
            ep = pj.epoch_subject(p, s, log=log)
            return _epoch_result(p, s, ep)

        self.run(f"分段 {s.name}", work, self._show_epoch_result)

    def epoch_all(self):
        if not self.need_project(need_done=False):
            return
        self._save_pipe()
        p = self.project
        todo = [s for s in p.subjects if any(r.status == pj.REC_DONE for r in s.recordings)]
        if not todo:
            QMessageBox.information(self, "还不能分段", "还没有预处理好的记录。先到「预处理」处理。")
            return

        def work():
            rows = []
            for s in todo:
                try:
                    ep = pj.epoch_subject(p, s, log=log)
                    rows.append([s.name, str(len(ep)), " / ".join(f"{k} {v}" for k, v in s.n_trials.items())])
                except Exception as e:  # noqa: BLE001
                    rows.append([s.name, "失败", str(e)[:80]])
                p.save()
            res = analysis.Result("分段", f"批量分段 · {len(todo)} 个被试")
            ok = sum(1 for x in rows if x[1].isdigit())
            res.metrics = [("成功", str(ok), ""), ("失败", str(len(rows) - ok), "")]
            res.table = (["被试", "保留段数", "各条件"], rows)
            res.table_title = "各被试分段结果"
            return res

        self.run("批量分段", work, self._show_epoch_result)

    def _show_epoch_result(self, res):
        self.project.save()
        self.ctx.project_changed("分段完成，可以去 ERP / 时频 / 解码 / RSA 分析了")
        d = QDialog(self)
        d.setWindowTitle(res.title)
        d.resize(820, 620)
        from .widgets import ResultView
        v = ResultView(QWidget())
        v.show_result(res)
        lay = QVBoxLayout(d)
        lay.addWidget(v)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.exec()


def _epoch_result(p, s, ep):
    res = analysis.Result("分段", f"{s.name} · 分段完成")
    dropped = sum(1 for d in ep.drop_log if d and "IGNORED" not in d)
    worst = pj.drop_summary(ep, 5)
    res.metrics = [("保留", str(len(ep)), " / ".join(f"{k} {v}" for k, v in s.n_trials.items())),
                   ("剔除", str(dropped), f"阈值 {p.pipeline.reject_uv:g} µV" if p.pipeline.reject_uv else "不剔除"),
                   ("时间窗", f"{ep.tmin:g} – {ep.tmax:g} s", f"{ep.info['sfreq']:g} Hz")]
    if worst:
        res.notes = ["剔除最多的通道：" + "、".join(f"{c}（{n} 次）" for c, n in worst) +
                     "。如果某个通道特别多，到「预处理」把它标成坏导再处理，能保留更多试次。"]
    if len(ep):
        evk = ep.average()
        res.figures.append(("所有分段平均", evk.plot(show=False, spatial_colors=evk.get_montage() is not None)))
    return res


def _unit_scale(raw, ch):
    """MNE 把脑电通道换算成伏特；标记、计数这类通道要换回原始数值。"""
    t = raw.get_channel_types(picks=[ch])[0]
    return 1e6 if t in ("eeg", "eog", "ecg", "emg", "misc") else 1.0


def _pair(a, b):
    w = QWidget()
    w.setLayout(hbox(a, label("到", "Muted"), b, spacing=6))
    return w


class TableImportDialog(QDialog):
    """从任意表格导入事件：选时间列、事件名列，预览后导入。"""

    def __init__(self, parent, rec, header, guess_path, spec0, n_recs):
        super().__init__(parent)
        self.setWindowTitle("从表格导入事件")
        self.resize(860, 680)
        self.rec, self.h = rec, header
        self.tab = None
        self.result_events = ev.empty()
        self.path = guess_path or ""
        lay = QVBoxLayout(self)
        self.file = line(self.path, "选择一个 CSV / TXT / Excel 表格")
        lay.addLayout(hbox(label("表格", "Sub"), self.file, btn("选择…", self._pick, "ghost", "mdi6.folder-open-outline")))
        if guess_path:
            lay.addWidget(label("已自动找到同名的表格文件，确认一下对不对。", "Muted"))
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setMinimumHeight(200)
        lay.addWidget(self.preview, 1)
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.skip = ispin(0, 0, 1000)
        self.tcol, self.lcol, self.dcol = combo([]), combo([]), combo([])
        self.unit = combo(ev.TIME_UNITS)
        self.offset = fspin(0, -1e5, 1e5, 0.01, 3, " s")
        f.addRow("跳过开头几行", self.skip)
        f.addRow("时间在哪一列", self.tcol)
        f.addRow("时间单位", self.unit)
        f.addRow("事件名在哪一列", self.lcol)
        f.addRow("时长在哪一列", self.dcol)
        f.addRow("整体平移", self.offset)
        lay.addLayout(f)
        self.info = label("", "Sub", wrap=True)
        lay.addWidget(self.info)
        self.replace = checkbox("替换这条记录现有的事件（不勾选就是追加）", True)
        lay.addWidget(self.replace)
        self.apply_all = checkbox(f"按同样的方式给其他记录也导入（按文件名规律自动找表格，共 {n_recs} 条记录）", n_recs > 1)
        lay.addWidget(self.apply_all)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("导入")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._spec0 = spec0
        for w in (self.tcol, self.lcol, self.dcol, self.unit):
            w.currentTextChanged.connect(self._update)
        self.offset.valueChanged.connect(self._update)
        self.skip.valueChanged.connect(self._read)
        self.file.editingFinished.connect(self._read)
        if self.path:
            self._read()

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择事件表格", os.path.dirname(self.rec.source), ev.TABLE_FILTER)
        if p:
            self.file.setText(p)
            self._read()

    def _read(self):
        self.path = self.file.text().strip().strip('"')
        if not self.path or not os.path.exists(self.path):
            return
        try:
            self.tab = ev.read_table(self.path, self.skip.value())
        except Exception as e:  # noqa: BLE001
            self.info.setText(f"读不了这个表格：{e}")
            self.tab = None
            return
        t = self.tab
        n = min(len(t), 8)
        self.preview.setColumnCount(len(t.columns))
        self.preview.setRowCount(n)
        self.preview.setHorizontalHeaderLabels(list(t.columns))
        for i in range(n):
            for j, c in enumerate(t.columns):
                self.preview.setItem(i, j, QTableWidgetItem(str(t.iloc[i][c])))
        self.preview.resizeColumnsToContents()
        cols = list(t.columns)
        g = ev.guess_columns(t)
        sp = self._spec0 if self._spec0 and self._spec0.get("time_col") in cols else None
        for cb, items, cur in [(self.tcol, cols, (sp or g)["time_col"]),
                               (self.lcol, [NONE] + cols, (sp or g)["label_col"] or NONE),
                               (self.dcol, [NONE] + cols, (sp or g).get("dur_col") or NONE)]:
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(items)
            cb.setCurrentText(cur)
            cb.blockSignals(False)
        self.unit.setCurrentText((sp or g)["unit"])
        if sp:
            self.offset.setValue(float(sp.get("offset", 0)))
        self._update()

    def spec(self):
        return dict(time_col=self.tcol.currentText(),
                    label_col="" if self.lcol.currentText() == NONE else self.lcol.currentText(),
                    dur_col="" if self.dcol.currentText() == NONE else self.dcol.currentText(),
                    unit=self.unit.currentText(), offset=self.offset.value(), skip=self.skip.value())

    def _update(self):
        if self.tab is None or not self.tcol.currentText():
            return
        msgs = []
        try:
            self.result_events = ev.table_to_events(self.tab, self.spec(), self.h["sfreq"], self.h["start"],
                                                    self.h["dur"], log=msgs.append)
        except Exception as e:  # noqa: BLE001
            self.result_events = ev.empty()
            self.info.setText(f"<span style='color:{C['danger']}'>{e}</span>")
            self.info.setTextFormat(Qt.RichText)
            return
        df = self.result_events
        kinds = ev.summary(df)
        txt = (f"将导入 {len(df)} 个事件、{len(kinds)} 种：" +
               "，".join(f"{n}×{c}" for n, c, _ in kinds[:10]) + ("…" if len(kinds) > 10 else ""))
        if len(df):
            txt += f"。时间从 {df['onset'].min():.1f} 到 {df['onset'].max():.1f} 秒（记录长 {self.h['dur']:.0f} 秒）"
        if msgs:
            txt += "。" + "；".join(msgs)
        self.info.setTextFormat(Qt.PlainText)
        self.info.setText(txt)

    def _ok(self):
        if not len(self.result_events):
            QMessageBox.information(self, "没有事件", "按现在的设置导入不了任何事件，检查一下时间列和单位。")
            return
        self.accept()


class ChannelEventDialog(QDialog):
    def __init__(self, parent, ch_names):
        super().__init__(parent)
        self.setWindowTitle("从通道读取事件")
        lay = QVBoxLayout(self)
        lay.addWidget(label("有些设备把触发码记在一个通道里（如 Status、STI 014、MarkerValueInt）。"
                            "选这个通道，数值每次从 0 变成非 0 的时刻记为一个事件，事件名就是这个数值。", "Sub", wrap=True))
        f = QFormLayout()
        self.ch = combo(ch_names)
        for key in ("marker", "status", "sti", "trig", "event"):
            hit = next((c for c in ch_names if key in c.lower()), None)
            if hit:
                self.ch.setCurrentText(hit)
                break
        self.prefix = line("", "可不填，例如 S")
        f.addRow("通道", self.ch)
        f.addRow("事件名前缀", self.prefix)
        lay.addLayout(f)
        self.replace = checkbox("替换现有事件（不勾选就是追加）", False)
        lay.addWidget(self.replace)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def channel(self):
        return self.ch.currentText()
