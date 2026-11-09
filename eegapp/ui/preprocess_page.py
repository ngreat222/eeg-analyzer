"""预处理页：一套设置用于所有被试；可以逐个检查（波形、功率谱、ICA 成分），也可以一键批量处理。"""
import os
from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QWidget

from ..core import analysis, project as pj, single
from ..core.single import STANDARD_MONTAGES, parse_floats, parse_names
from ..theme import C
from .base import BasePage
from .common import combo, fspin, ispin, line, log
from .widgets import CheckList, EmptyState, ResultView, btn, checkbox, hbox, icon, label

MONTAGES = ["自动识别"] + STANDARD_MONTAGES + ["不设置"]


class PreprocessPage(BasePage):
    title = "预处理"
    subtitle = "滤波 · 坏导 · ICA 去伪迹 · 分段。设置一次，所有被试通用"
    side_width = 350

    def __init__(self, ctx):
        super().__init__(ctx)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.subs = QListWidget()
        self.subs.setFixedWidth(190)
        self.subs.currentRowChanged.connect(self._select)
        row.addWidget(self.subs)
        self.view = ResultView(EmptyState(
            "mdi6.tune-variant", "把数据整理干净",
            "左边选一个被试，点「载入查看」看功率谱和电极位置；需要时拟合 ICA 挑出眼电等伪迹成分。"
            "设置满意后点「处理当前被试」，或直接「批量处理全部」。"))
        row.addWidget(self.view, 1)
        self.body.addLayout(row, 1)
        self.raw = self.ica = self.raw_ica = None
        self._preview_for = None
        self._preview_pipe = None

        s = self.section("当前被试")
        self.cur = s.widget(label("—", "H2"))
        self.bads = s.row("坏导", line("", "例：FC5, T8"))
        self.locs = s.row("电极位置文件", line("", "可不填，自动按标准 10-20 识别"))
        s.widget(btn("选择位置文件…", self._pick_locs, "ghost", "mdi6.folder-open-outline"))
        self.rename = s.widget(checkbox("用位置文件里的通道名替换数据里的通道名"))
        self.excl = s.row("ICA 去除成分", line("", "留空 = 按眼电自动识别"))

        s = self.section("通道与事件", expanded=False)
        self.montage = s.row("电极位置", combo(MONTAGES))
        self.reref = s.row("重参考", combo(["不改变", "平均参考", "指定通道"]))
        self.reref_ch = s.row("参考通道", line("", "例：M1, M2"))
        self.emap = s.row("事件码改名", line("", "例：1=rt, 2=square"))

        s = self.section("滤波")
        self.notch = s.row("陷波", fspin(50, 0, 1000, 10, 1, " Hz"))
        self.harm = s.widget(checkbox("同时去除谐波"))
        self.lf = s.row("高通", fspin(0.1, 0, 1000, 0.1, 2, " Hz"))
        self.hf = s.row("低通", fspin(30, 0, 5000, 5, 1, " Hz"))
        self.rs = s.row("重采样", fspin(0, 0, 20000, 50, 1, " Hz"))
        s.widget(label("填 0 表示不做这一步。国内工频 50 Hz，手册示例数据是 60 Hz。", "Muted", wrap=True))

        s = self.section("坏导与 ICA")
        self.interp = s.widget(checkbox("插值坏导", True))
        self.ica_on = s.widget(checkbox("用 ICA 去除伪迹", True))
        self.ica_n = s.row("成分数", line("", "留空 = 全部"))
        self.ica_m = s.row("算法", combo(["fastica", "infomax", "picard"]))
        self.ica_auto = s.widget(checkbox("按眼电通道自动识别要去除的成分", True))

        s = self.section("分段")
        self.events = s.row("事件", CheckList(110))
        self.tmin, self.tmax = fspin(-0.2, -60, 60, 0.1, 3, " s"), fspin(0.8, -60, 60, 0.1, 3, " s")
        s.row("时间", self._pair(self.tmin, self.tmax))
        self.bl_on = s.widget(checkbox("基线校正", True))
        self.b0, self.b1 = fspin(-0.2, -60, 60, 0.05, 3, " s"), fspin(0, -60, 60, 0.05, 3, " s")
        s.row("基线", self._pair(self.b0, self.b1))
        self.rej = s.row("振幅剔除", fspin(150, 0, 1e5, 10, 0, " µV"))
        self.end_side()

        self.action_row(btn("载入查看", self.preview, None, "mdi6.eye-outline"),
                        btn("波形", self.browse, None, "mdi6.waveform"),
                        btn("ICA", self.fit_ica, None, "mdi6.atom"))
        self.action_row(btn("处理当前被试", self.process_one, "primary", "mdi6.check"),
                        btn("批量处理全部", self.process_all, "accent", "mdi6.lightning-bolt"))

    @staticmethod
    def _pair(a, b):
        w = QWidget()
        w.setLayout(hbox(a, label("到", "Muted"), b, spacing=6))
        return w

    # ------------------------------------------------------------ 设置 <-> 界面
    def _load_pipe(self):
        p = self.project.pipeline
        self.montage.setCurrentText(p.montage)
        self.reref.setCurrentText(p.reref)
        self.reref_ch.setText(p.reref_channels)
        self.emap.setText(p.event_map)
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
        self.tmin.setValue(p.tmin)
        self.tmax.setValue(p.tmax)
        self.bl_on.setChecked(p.baseline)
        self.b0.setValue(p.bl0)
        self.b1.setValue(p.bl1)
        self.rej.setValue(p.reject_uv)

    def _save_pipe(self):
        p = self.project.pipeline
        p.montage, p.reref, p.reref_channels = self.montage.currentText(), self.reref.currentText(), self.reref_ch.text()
        p.event_map = self.emap.text()
        p.notch, p.harmonics, p.l_freq, p.h_freq, p.resample = (self.notch.value(), self.harm.isChecked(),
                                                                self.lf.value(), self.hf.value(), self.rs.value())
        p.interpolate, p.ica, p.ica_n = self.interp.isChecked(), self.ica_on.isChecked(), self.ica_n.text().strip()
        p.ica_method, p.ica_auto_eog = self.ica_m.currentText(), self.ica_auto.isChecked()
        all_ev = [self.events.item(i).text() for i in range(self.events.count())]
        sel = self.events.checked()
        p.events = [] if set(sel) == set(all_ev) else sel
        p.tmin, p.tmax, p.baseline = self.tmin.value(), self.tmax.value(), self.bl_on.isChecked()
        p.bl0, p.bl1, p.reject_uv = self.b0.value(), self.b1.value(), self.rej.value()
        s = self._subject()
        if s is not None:
            s.bads = parse_names(self.bads.text())
            s.locs = self.locs.text().strip()
            s.rename_from_locs = self.rename.isChecked()
            ex = self.excl.text().strip()
            s.ica_manual = bool(ex)
            if ex:
                s.ica_exclude = [int(x) for x in parse_floats(ex)]
        self.project.save()

    def _subject(self):
        it = self.subs.currentItem()
        if it is None or self.project is None:
            return None
        return self.project.get(it.data(Qt.UserRole))

    # ------------------------------------------------------------ 显示
    def on_show(self, subject=None):
        super().on_show()
        p = self.project
        self.subs.blockSignals(True)
        cur = subject or (self.subs.currentItem().data(Qt.UserRole) if self.subs.currentItem() else None)
        self.subs.clear()
        if p is not None:
            self._load_pipe()
            for s in p.subjects:
                done = s.status == pj.STATUS_DONE
                it = QListWidgetItem(icon("mdi6.check-circle" if done else "mdi6.circle-outline",
                                          C["mint"] if done else C["amber"]), s.name)
                it.setData(Qt.UserRole, s.name)
                it.setToolTip(s.source or s.note)
                self.subs.addItem(it)
                if s.name == cur:
                    self.subs.setCurrentItem(it)
            if self.subs.currentRow() < 0 and self.subs.count():
                raw_first = next((i for i, s in enumerate(p.subjects) if s.source), 0)
                self.subs.setCurrentRow(raw_first)
        self.subs.blockSignals(False)
        self._select()

    def _select(self, *_):
        s = self._subject()
        if s is None:
            self.cur.setText("—")
            return
        self.cur.setText(f"{s.name}　·　{s.status}")
        self.bads.setText(", ".join(s.bads))
        self.locs.setText(s.locs)
        self.rename.setChecked(s.rename_from_locs)
        self.excl.setText(", ".join(map(str, s.ica_exclude)) if s.ica_manual else "")
        if self._preview_for != s.name:
            self.raw = self.ica = self.raw_ica = None
            self._preview_for = None

    def _pick_locs(self):
        p, _ = QFileDialog.getOpenFileName(self, "电极位置文件", os.path.expanduser("~/Desktop"),
                                           "电极位置 (*.locs *.loc *.elc *.sfp *.elp *.txt *.csd *.bvef);;所有文件 (*)")
        if p:
            self.locs.setText(os.path.normpath(p))

    def _need_raw_subject(self):
        if not self.need_project(need_done=False):
            return None
        s = self._subject()
        if s is None:
            QMessageBox.information(self, "被试", "请先在「项目」添加数据。")
            return None
        if not s.source:
            QMessageBox.information(self, "无需预处理", f"{s.name} 导入时已经是分段数据，可以直接去分析。")
            return None
        return s

    # ------------------------------------------------------------ 操作
    def preview(self):
        s = self._need_raw_subject()
        if s is None:
            return
        self._save_pipe()
        pipe = self.project.pipeline

        def work():
            raw = pj.load_raw(s, pipe, log=log)
            res = analysis.Result("预处理", f"{s.name} · 预处理预览")
            res.figures.append(("滤波前功率谱", single.plot_psd(raw, 0, 80)))
            pj.filter_raw(raw, pipe, log=log)
            res.figures.append(("滤波后功率谱", single.plot_psd(raw, 0, min(80, raw.info["sfreq"] / 2))))
            if raw.get_montage() is not None:
                res.figures.append(("电极位置", single.plot_sensors(raw)))
            ev = single.event_table(raw)
            res.metrics = [("通道", str(len(raw.ch_names)), f"{raw.info['sfreq']:g} Hz，{raw.times[-1]:.0f} s"),
                           ("事件", "、".join(f"{n}×{c}" for n, c in ev[:4]) or "无", f"{len(ev)} 种"),
                           ("电极位置", "已设置" if raw.get_montage() is not None else "未设置",
                            "坏导 " + (", ".join(raw.info["bads"]) or "无"))]
            return raw, res, ev

        def done(r):
            self.raw, res, ev = r
            self.ica = self.raw_ica = None
            self._preview_for, self._preview_pipe = s.name, asdict(self.project.pipeline)
            names = [n for n, _ in ev]
            pe = self.project.pipeline.events
            self.events.set_items(names, checked=[n for n in pe if n in names] or names)
            self.view.show_result(res)

        self.run(f"载入 {s.name}", work, done)

    def browse(self):
        if self.raw is None:
            QMessageBox.information(self, "先载入", "先点「载入查看」。")
            return
        s = self._subject()
        try:
            b = self.raw.plot(duration=10, n_channels=min(32, len(self.raw.ch_names)), block=False, show=True,
                              theme="dark", title=f"{s.name}：点击通道名标记坏导，关闭后生效")

            def closed():
                s.bads = list(self.raw.info["bads"])
                self.bads.setText(", ".join(s.bads))
                self.project.save()
                log(f"{s.name} 坏导：{s.bads or '无'}")
            self.ctx.keep_window(b, closed)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "波形浏览器", str(e))

    def fit_ica(self):
        if self.raw is None:
            QMessageBox.information(self, "先载入", "先点「载入查看」。")
            return
        s = self._subject()
        self._save_pipe()
        raw, pipe = self.raw, self.project.pipeline

        def work():
            ica, raw_ica = pj.fit_ica(raw, pipe, log=log)
            sug = pj.auto_exclude(ica, raw, log=log)
            res = analysis.Result("预处理", f"{s.name} · ICA 成分")
            figs = ica.plot_components(show=False)
            figs = figs if isinstance(figs, list) else [figs]
            for i, f in enumerate(figs):
                res.figures.append((f"成分地形图 {i + 1}", f))
            if sug:
                for i, f in enumerate(ica.plot_properties(raw, picks=sug[:3], show=False)):
                    res.figures.append((f"成分 {sug[i]} 属性", f))
                res.figures.append(("去除前后对比", ica.plot_overlay(raw_ica, exclude=sug, show=False)))
            res.metrics = [("成分数", str(ica.n_components_), pipe.ica_method),
                           ("建议去除", ", ".join(map(str, sug)) or "未识别到", "根据 EOG 通道相关"),
                           ("怎么改", "填到“ICA 去除成分”", "或在成分波形里点击成分")]
            return ica, raw_ica, sug, res

        def done(r):
            self.ica, self.raw_ica, sug, res = r
            if not self.excl.text().strip() and sug:
                self.excl.setPlaceholderText(f"自动：{', '.join(map(str, sug))}")
            self.view.show_result(res)
            try:
                b = self.ica.plot_sources(self.raw_ica, block=False, show=True, theme="dark",
                                          title=f"{s.name} ICA 成分：点击成分名标记去除")
                self.ctx.keep_window(b, lambda: self.excl.setText(", ".join(map(str, self.ica.exclude)))
                                     if self.ica.exclude else None)
            except Exception as e:  # noqa: BLE001
                log(f"成分波形浏览器未打开：{e}")

        self.run(f"{s.name} 拟合 ICA", work, done)

    def _after(self, msg):
        self.project.save()
        self.ctx.project_changed(msg)

    def process_one(self):
        s = self._need_raw_subject()
        if s is None:
            return
        self._save_pipe()
        proj = self.project
        use_preview = (self.raw is not None and self._preview_for == s.name
                       and self._preview_pipe == asdict(proj.pipeline))
        raw = self.raw.copy() if use_preview else None
        ica = self.ica if use_preview else None
        if self.raw is not None and not use_preview:
            log("设置在载入后改过，按新设置重新读取处理")

        def work():
            ep = pj.run_pipeline(proj, s, log=log, raw=raw, ica=ica)
            res = analysis.Result("预处理", f"{s.name} · 预处理完成")
            dropped = sum(1 for d in ep.drop_log if d)
            res.metrics = [("保留试次", str(len(ep)), " / ".join(f"{k} {v}" for k, v in s.n_trials.items())),
                           ("剔除试次", str(dropped), f"阈值 {proj.pipeline.reject_uv:g} µV"),
                           ("ICA 去除", ", ".join(map(str, s.ica_exclude)) or "无",
                            "手动指定" if s.ica_manual else "自动识别")]
            res.figures.append(("分段功率谱", single.plot_epochs_psd(ep, 0, 60)))
            ev = ep.average()
            res.figures.append(("所有试次平均", ev.plot(show=False, spatial_colors=ev.get_montage() is not None)))
            return res

        self.run(f"处理 {s.name}", work, lambda res: (self.view.show_result(res),
                                                    self._after(f"{s.name} 预处理完成，可以去分析了")))

    def process_all(self):
        if not self.need_project(need_done=False):
            return
        self._save_pipe()
        proj = self.project
        todo = [s for s in proj.subjects if s.source and s.status != pj.STATUS_DONE]
        raw_all = [s for s in proj.subjects if s.source]
        if not raw_all:
            QMessageBox.information(self, "没有原始数据", "项目里没有需要预处理的原始数据。")
            return
        if not todo:
            if QMessageBox.question(self, "全部已处理", "所有原始数据都处理过了。用当前设置全部重新处理吗？") \
                    != QMessageBox.Yes:
                return
            todo = raw_all

        def work():
            rows, failed = [], []
            for i, s in enumerate(todo):
                log(f"—— 批量处理 {i + 1}/{len(todo)}：{s.name}")
                try:
                    ep = pj.run_pipeline(proj, s, log=log)
                    rows.append([s.name, str(len(ep)), ", ".join(s.bads) or "—",
                                 ", ".join(map(str, s.ica_exclude)) or "—"])
                except Exception as e:  # noqa: BLE001
                    failed.append(s.name)
                    rows.append([s.name, "失败", str(e)[:60], ""])
                    log(f"{s.name} 失败：{e}")
                proj.save()
            res = analysis.Result("预处理", f"批量预处理 · {len(todo)} 个被试")
            res.metrics = [("成功", str(len(todo) - len(failed)), ""), ("失败", str(len(failed)), ", ".join(failed))]
            res.table = (["被试", "保留试次", "坏导", "ICA 去除"], rows)
            res.table_title = "各被试处理结果"
            return res

        self.run("批量预处理", work, lambda res: (self.view.show_result(res),
                                               self._after("批量预处理完成")))
