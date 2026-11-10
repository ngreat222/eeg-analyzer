"""预处理页：只处理连续数据（通道、滤波、坏导、重参考、ICA），不需要事件。

左边选一条记录，中间是这条记录的步骤清单和检查结果，右边是设置。
处理结果是“干净的连续数据”，事件和分段在「事件与分段」页做。
"""
import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..core import analysis, project as pj, single
from ..core.single import parse_floats, parse_names
from .. import theme
from ..theme import C
from .base import BasePage
from .common import combo, fspin, line, log
from .widgets import EmptyState, ResultView, StepList, btn, checkbox, hbox, icon, label, side_list

MONTAGES = ["自动识别", "不设置"] + single.STANDARD_MONTAGES
PRE_FIELDS = ("montage", "reref", "reref_channels", "notch", "harmonics", "l_freq", "h_freq", "resample",
              "interpolate", "ica", "ica_n", "ica_method", "ica_auto_eog")


class PreprocessPage(BasePage):
    title = "预处理"
    subtitle = "把连续数据整理干净：通道、滤波、坏导、ICA。这一步不需要事件"

    def __init__(self, ctx):
        super().__init__(ctx)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.recs = side_list(210)
        self.recs.currentRowChanged.connect(self._select)
        row.addWidget(self.recs)
        center = QVBoxLayout()
        center.setSpacing(12)
        self.steps = StepList("这条记录的处理步骤")
        center.addWidget(self.steps)
        self.view = ResultView(EmptyState(
            "mdi6.tune-variant", "把数据整理干净",
            "左边选一条记录，点「载入查看」检查通道、功率谱和电极位置；需要时拟合 ICA 挑出眼电成分。"
            "设置满意后点「处理这条记录」，或者直接「批量处理全部」。"))
        center.addWidget(self.view, 1)
        row.addLayout(center, 1)
        self.body.addLayout(row, 1)
        self.raw = self.ica = self.raw_ica = None
        self._sig = None          # 当前载入的数据对应的设置；设置变了就重新读
        self._non_eeg_hint = {}   # 记录 id → {通道: 原因}

        s = self.section("当前记录")
        self.cur = s.widget(label("—", "H2"))
        self.chan = s.widget(label("非脑电通道：载入后自动识别", "Sub", wrap=True))
        s.widget(btn("检查通道…", self.check_channels, "accent", "mdi6.format-list-checks"))
        self.locs = s.row("电极位置文件（这个被试）", line("", "可不填，自动按标准 10-20 识别"))
        s.widget(btn("选择位置文件…", self._pick_locs, "ghost", "mdi6.folder-open-outline"))
        self.rename = s.widget(checkbox("用位置文件里的通道名"))
        self.rename.setToolTip("按顺序用位置文件里的通道名替换数据里的通道名")
        self.montage = s.row("电极位置", combo(MONTAGES))

        s = self.section("滤波")
        self.notch = s.row("陷波", fspin(50, 0, 1000, 10, 1, " Hz"))
        self.harm = s.widget(checkbox("同时去除谐波"))
        self.lf = s.row("高通", fspin(0.1, 0, 1000, 0.1, 2, " Hz"))
        self.hf = s.row("低通", fspin(30, 0, 5000, 5, 1, " Hz"))
        self.rs = s.row("重采样", fspin(0, 0, 20000, 50, 1, " Hz"))
        s.widget(label("填 0 表示不做这一步。国内工频 50 Hz。做时频分析时低通要高于关心的频率。", "Muted", wrap=True))

        s = self.section("坏导与参考")
        self.bads = s.row("坏导", line("", "例：FC5, T8"))
        s.widget(btn("自动检测坏导", self.detect_bads, "accent", "mdi6.radar"))
        self.interp = s.widget(checkbox("插值坏导", True))
        self.reref = s.row("重参考", combo(["不改变", "平均参考", "指定通道"]))
        self.reref_ch = s.row("参考通道", line("", "例：M1, M2"))

        s = self.section("ICA 去伪迹")
        self.ica_on = s.widget(checkbox("用 ICA 去除伪迹", True))
        self.ica_n = s.row("成分数", line("", "留空 = 全部"))
        self.ica_m = s.row("算法", combo(["fastica", "infomax", "picard"]))
        self.ica_auto = s.widget(checkbox("自动识别眼电成分", True))
        s.widget(label("没有眼电通道时，用 Fp1、Fp2 等前额电极代替。", "Muted", wrap=True))
        self.excl = s.row("去除成分", line("", "留空 = 用自动识别的结果"))
        self.end_side()

        self.action_row(btn("载入查看", self.preview, None, "mdi6.eye-outline"),
                        btn("波形", self.browse, None, "mdi6.waveform"),
                        btn("ICA", self.fit_ica, None, "mdi6.atom"))
        self.action_row(btn("处理这条记录", self.process_one, "primary", "mdi6.check"),
                        btn("批量处理全部", self.process_all, None, "mdi6.lightning-bolt-outline"))

    # ------------------------------------------------------------ 设置 <-> 界面
    def _load_pipe(self):
        p = self.project.pipeline
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

    def _save_pipe(self):
        p = self.project.pipeline
        p.montage, p.reref, p.reref_channels = self.montage.currentText(), self.reref.currentText(), self.reref_ch.text()
        p.notch, p.harmonics, p.l_freq, p.h_freq, p.resample = (self.notch.value(), self.harm.isChecked(),
                                                                self.lf.value(), self.hf.value(), self.rs.value())
        p.interpolate, p.ica, p.ica_n = self.interp.isChecked(), self.ica_on.isChecked(), self.ica_n.text().strip()
        p.ica_method, p.ica_auto_eog = self.ica_m.currentText(), self.ica_auto.isChecked()
        cur = self._current()
        if cur is not None:
            s, r = cur
            r.bads = parse_names(self.bads.text())
            s.locs = self.locs.text().strip()
            s.rename_from_locs = self.rename.isChecked()
            ex = self.excl.text().strip()
            r.ica_manual = bool(ex)
            if ex:
                r.ica_exclude = [int(x) for x in parse_floats(ex)]
        self.project.save()

    def _signature(self, s, r):
        """决定“载入的数据还能不能直接用”的所有设置。坏导、通道、滤波任一变化都要重新读。"""
        p = self.project.pipeline
        return json.dumps([r.id, r.bads, r.non_eeg, s.locs, s.rename_from_locs,
                           {k: getattr(p, k) for k in PRE_FIELDS if not k.startswith("ica")}],
                          ensure_ascii=False, sort_keys=True)

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
        self.recs.blockSignals(True)
        cur = subject or (self.recs.currentItem().data(Qt.UserRole) if self.recs.currentItem() else None)
        self.recs.clear()
        if p is not None:
            self._load_pipe()
            for s, r in p.recordings():
                done = r.status == pj.REC_DONE
                it = QListWidgetItem(icon("mdi6.check-circle-outline" if done else "mdi6.circle-outline",
                                          C["ok"] if done else C["muted"]),
                                     f"{s.name}" + (f"  ·  {r.condition}" if r.condition else ""))
                it.setData(Qt.UserRole, r.id)
                it.setToolTip(r.source)
                self.recs.addItem(it)
                if r.id == cur or (cur and s.name == cur and self.recs.currentRow() < 0):
                    self.recs.setCurrentItem(it)
            if self.recs.currentRow() < 0 and self.recs.count():
                self.recs.setCurrentRow(0)
        self.recs.blockSignals(False)
        self._select()

    def _select(self, *_):
        cur = self._current()
        if cur is None:
            self.cur.setText("—")
            self.steps.set([("off", "提示", "项目里还没有原始数据。到「项目」页导入文件或文件夹。", "")])
            return
        s, r = cur
        self.cur.setText(f"{s.name}" + (f" · {r.condition}" if r.condition else ""))
        self.bads.setText(", ".join(r.bads))
        self.locs.setText(s.locs)
        self.rename.setChecked(s.rename_from_locs)
        self.excl.setText(", ".join(map(str, r.ica_exclude)) if r.ica_manual else "")
        self.excl.setPlaceholderText("留空 = 用自动识别的结果")
        if self._sig is None or json.loads(self._sig)[0] != r.id:
            self.raw = self.ica = self.raw_ica = None
            self._sig = None
            self.view.reset()
        self._update_chan_label(r)
        self._update_steps()

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
        done = r.status == pj.REC_DONE
        loaded = self.raw is not None
        hint = self._non_eeg_hint.get(r.id)
        if r.non_eeg is not None:
            ch = ("done", f"去掉 {len(r.non_eeg)} 个非脑电通道" if r.non_eeg else "全部通道都是脑电", "已确认")
        elif hint is not None:
            ch = ("wait", f"自动识别出 {len(hint)} 个非脑电通道", "待确认")
        else:
            ch = ("todo", "载入后自动识别非脑电通道（时间戳、电量、接触质量等）", "")
        filt = "，".join(x for x in [
            f"高通 {p.l_freq:g} Hz" if p.l_freq else "不做高通（会去掉直流偏置）",
            f"低通 {p.h_freq:g} Hz" if p.h_freq else "",
            f"陷波 {p.notch:g} Hz" if p.notch else "", f"重采样 {p.resample:g} Hz" if p.resample else ""] if x)
        bads = ("done" if r.bads else "todo",
                ("坏导 " + ", ".join(r.bads) + ("，插值" if p.interpolate else "，只标记")) if r.bads
                else "没有标记坏导。可以点「自动检测坏导」", "已标记" if r.bads else "")
        if not p.ica:
            ica = ("off", "不做 ICA", "")
        elif r.ica_manual:
            ica = ("done", f"去除成分 {', '.join(map(str, r.ica_exclude)) or '无'}（手动指定）", "已指定")
        elif done:
            ica = ("done", f"自动去除成分 {', '.join(map(str, r.ica_exclude)) or '无'}", "")
        else:
            ica = ("todo", "处理时拟合 ICA，自动识别眼电成分；也可以先点「ICA」检查", "")
        self.steps.set([
            (ch[0], "通道", ch[1], ch[2]),
            ("done" if done or loaded else "todo", "滤波", filt, ""),
            (bads[0], "坏导", bads[1], bads[2]),
            ("done" if p.reref != "不改变" else "off", "参考",
             {"平均参考": "平均参考", "指定通道": f"参考 {p.reref_channels}"}.get(p.reref, "保持原参考"), ""),
            (ica[0], "ICA", ica[1], ica[2]),
            ("done" if done else "todo", "保存", "已保存干净的连续数据，可以去「事件与分段」" if done
             else "还没处理", "完成" if done else ""),
        ])

    def _pick_locs(self):
        p, _ = QFileDialog.getOpenFileName(self, "电极位置文件", os.path.expanduser("~/Desktop"),
                                           "电极位置 (*.locs *.loc *.elc *.sfp *.elp *.txt *.csd *.bvef);;所有文件 (*)")
        if p:
            self.locs.setText(os.path.normpath(p))

    def _need_rec(self):
        if not self.need_project(need_done=False):
            return None
        cur = self._current()
        if cur is None:
            QMessageBox.information(self, "没有原始数据", "请先在「项目」页导入原始数据（文件或文件夹）。")
            return None
        return cur

    # ------------------------------------------------------------ 操作
    def _load(self, s, r, with_figs=True):
        """后台读取、设置通道并滤波（坏导按设置插值）。返回 (raw, 非脑电通道建议, 检查结果)。"""
        pipe = self.project.pipeline
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
        self._save_pipe()
        sig = self._signature(s, r)

        def done(out):
            self.raw, hint, res = out
            self._non_eeg_hint[r.id] = hint
            self.ica = self.raw_ica = None
            self._sig = sig
            self.view.show_result(res)
            self._update_chan_label(r)
            self._update_steps()

        self.run(f"载入 {r.id}", lambda: self._load(s, r), done)

    def check_channels(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur

        def work():
            raw, _ = pj.reading.read_raw_any(r.source, log=log)
            pj._auto_types(raw)
            return raw.ch_names, raw.get_channel_types(), pj.suggest_non_eeg(raw)

        def done(out):
            names, types, hint = out
            self._non_eeg_hint[r.id] = hint
            current = r.non_eeg if r.non_eeg is not None else list(hint)
            d = ChannelDialog(self, names, types, hint, current, len(self.project.recordings()))
            if d.exec():
                non = d.non_eeg()
                targets = [x for _, x in self.project.recordings()] if d.apply_all.isChecked() else [r]
                redo = 0
                for x in targets:
                    if x.non_eeg is not None and sorted(x.non_eeg) != sorted(non) and x.status == pj.REC_DONE:
                        x.status = pj.REC_RAW          # 通道变了，需要重新处理
                        redo += 1
                    x.non_eeg = list(non)
                self.project.save()
                if redo:
                    log(f"{redo} 条已处理的记录通道设置变了，需要重新处理")
                log(f"非脑电通道（{len(non)} 个）已确认" + ("，用到所有记录" if len(targets) > 1 else ""))
            self._update_chan_label(r)
            self._update_steps()

        self.run(f"读取 {r.id} 的通道", work, done)

    def detect_bads(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        self._save_pipe()
        pipe = self.project.pipeline

        def work():
            raw, _ = pj.open_raw(s, r, pipe, log=log)
            raw.info["bads"] = []
            pj.filter_raw(raw, pipe, log=log, interpolate=False)
            return pj.suggest_bads(raw)

        def done(sug):
            res = analysis.Result("预处理", f"{r.id} · 坏导检测")
            if sug:
                res.table = (["通道", "原因"], [[k, v] for k, v in sug.items()])
                res.table_title = "建议标为坏导"
                res.notes = ["已把建议填进右边的“坏导”，可以删改；在「波形」里也可以逐个确认。"]
                self.bads.setText(", ".join(sug))
            else:
                res.notes = ["没有发现明显的坏导。"]
            res.metrics = [("建议坏导", str(len(sug)), "整段数据按 2 秒小窗和其他电极比较")]
            self.view.show_result(res)
            self._save_pipe()
            self._update_steps()

        self.run(f"检测 {r.id} 的坏导", work, done)

    def browse(self):
        if self.raw is None:
            QMessageBox.information(self, "先载入", "先点「载入查看」。")
            return
        s, r = self._current()
        try:
            b = self.raw.plot(duration=10, n_channels=min(32, len(self.raw.ch_names)), block=False, show=True,
                              theme=theme.BROWSER, title=f"{r.id}：点击通道名标记坏导，关闭后生效")

            def closed():
                r.bads = sorted(set(r.bads) | set(self.raw.info["bads"]))
                self.bads.setText(", ".join(r.bads))
                self.project.save()
                self._sig = None             # 坏导变了，处理时重新读取
                log(f"{r.id} 坏导：{r.bads or '无'}")
                self._update_steps()
            self.ctx.keep_window(b, closed)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "波形浏览器", str(e))

    def fit_ica(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        self._save_pipe()
        pipe = self.project.pipeline
        sig = self._signature(s, r)
        reuse = self.raw if (self.raw is not None and self._sig == sig) else None

        def work():
            raw = reuse
            if raw is None:
                raw, hint, _ = self._load(s, r, with_figs=False)
                self._non_eeg_hint[r.id] = hint
            ica, raw_ica = pj.fit_ica(raw, pipe, log=log)
            sug = pj.auto_exclude(ica, raw, pipe, log=log)
            res = analysis.Result("预处理", f"{r.id} · ICA 成分")
            figs = ica.plot_components(show=False)
            figs = figs if isinstance(figs, list) else [figs]
            for i, f in enumerate(figs):
                res.figures.append((f"成分地形图 {i + 1}", f))
            if sug:
                for i, f in enumerate(ica.plot_properties(raw, picks=sug[:3], show=False)):
                    res.figures.append((f"成分 {sug[i]} 属性", f))
                res.figures.append(("去除前后对比", ica.plot_overlay(raw_ica, exclude=sug, show=False)))
            res.metrics = [("成分数", str(ica.n_components_), pipe.ica_method),
                           ("建议去除", ", ".join(map(str, sug)) or "未识别到", "和眼电（或前额电极）相关的成分"),
                           ("怎么改", "填到“去除成分”", "或在成分波形里点击成分名")]
            return raw, ica, raw_ica, sug, res

        def done(out):
            self.raw, self.ica, self.raw_ica, sug, res = out
            self._sig = sig
            if sug:
                self.excl.setPlaceholderText(f"留空 = 自动：{', '.join(map(str, sug))}")
            self.view.show_result(res)
            self._update_steps()
            try:
                b = self.ica.plot_sources(self.raw_ica, block=False, show=True, theme=theme.BROWSER,
                                          title=f"{r.id} ICA 成分：点击成分名标记去除")
                self.ctx.keep_window(b, lambda: self.excl.setText(", ".join(map(str, self.ica.exclude)))
                                     if self.ica.exclude else None)
            except Exception as e:  # noqa: BLE001
                log(f"成分波形浏览器未打开：{e}")

        self.run(f"{r.id} 拟合 ICA", work, done)

    def _after(self, msg):
        self.project.save()
        self.ctx.project_changed(msg)

    def process_one(self):
        cur = self._need_rec()
        if cur is None:
            return
        s, r = cur
        self._save_pipe()
        proj = self.project
        use = self.raw is not None and self._sig == self._signature(s, r)
        raw = self.raw.copy() if use else None
        ica = self.ica if use else None
        if self.raw is not None and not use:
            log("设置或坏导在载入后改过，按新设置重新读取处理")

        def work():
            out = pj.preprocess_recording(proj, s, r, raw=raw, ica=ica, log=log)
            res = analysis.Result("预处理", f"{r.id} · 预处理完成")
            res.metrics = [("脑电通道", str(len(out.ch_names)), f"坏导 {', '.join(r.bads) or '无'}"),
                           ("ICA 去除", ", ".join(map(str, r.ica_exclude)) or "无",
                            "手动指定" if r.ica_manual else "自动识别"),
                           ("时长", f"{out.times[-1]:.0f} 秒", f"{out.info['sfreq']:g} Hz")]
            res.figures.append(("处理后功率谱", single.plot_psd(out, 0, min(60, out.info["sfreq"] / 2))))
            res.notes = ["干净的连续数据已保存。下一步到「事件与分段」添加事件并分段。"]
            return res

        def done(res):
            self.view.show_result(res)
            self._after(f"{r.id} 预处理完成")

        self.run(f"处理 {r.id}", work, done)

    def process_all(self):
        if not self.need_project(need_done=False):
            return
        self._save_pipe()
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
                                 ", ".join(map(str, r.ica_exclude)) or "—"])
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

        self.run("批量预处理", work, lambda res: (self.view.show_result(res), self._after("批量预处理完成")))


class ChannelDialog(QDialog):
    """确认哪些通道是脑电。打勾 = 脑电，保留；不打勾 = 去掉。"""

    def __init__(self, parent, names, types, hint, non_eeg, n_recs):
        super().__init__(parent)
        self.setWindowTitle("检查通道")
        self.resize(560, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"共 {len(names)} 个通道，自动识别出 {len(hint)} 个非脑电通道。"
                            "打勾的通道当作脑电保留，不打勾的处理时去掉。眼电、心电等按名字识别的通道会保留并标成对应类型。",
                            "Sub", wrap=True))
        self.table = QTableWidget(len(names), 3)
        self.table.setHorizontalHeaderLabels(["通道", "类型", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for i, (n, t) in enumerate(zip(names, types)):
            it = QTableWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if n in non_eeg else Qt.Checked)
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(t))
            self.table.setItem(i, 2, QTableWidgetItem(hint.get(n, "")))
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.resizeColumnsToContents()
        lay.addWidget(self.table, 1)
        lay.addLayout(hbox(btn("全选", lambda: self._all(True), "ghost"),
                           btn("全不选", lambda: self._all(False), "ghost"),
                           btn("恢复自动识别", lambda: self._auto(hint), "ghost"), "stretch"))
        self.apply_all = checkbox(f"同样的设置用到项目里所有记录（共 {n_recs} 条，同一设备时选这个）", n_recs > 1)
        lay.addWidget(self.apply_all)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _all(self, on):
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _auto(self, hint):
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            it.setCheckState(Qt.Unchecked if it.text() in hint else Qt.Checked)

    def non_eeg(self):
        return [self.table.item(i, 0).text() for i in range(self.table.rowCount())
                if self.table.item(i, 0).checkState() != Qt.Checked]
