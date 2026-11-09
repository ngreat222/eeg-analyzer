"""第 3 章多被试分析页面（统计方法来自第 2 章）。"""
import os

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QListWidget, QListWidgetItem, QMessageBox

from ..core import arrays, group, stats
from ..core.single import parse_floats
from .common import Page, button, check, combo, fspin, info_box, ispin, line, log, note
from .common import group as box
from .files_widget import ConditionDialog


def _selected_conditions(lw):
    return [lw.item(i).text() for i in range(lw.count()) if lw.item(i).checkState() == Qt.Checked]


def _fill_conditions(lw, names):
    prev = set(_selected_conditions(lw)) & set(names)  # 换了数据后旧的勾选作废，默认全选
    lw.clear()
    for n in names:
        it = QListWidgetItem(n)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if (n in prev or not prev) else Qt.Unchecked)
        lw.addItem(it)


def _cond_list():
    lw = QListWidget()
    lw.setMaximumHeight(110)
    return lw


def save_csv(parent, header, columns, default_name):
    p, _ = QFileDialog.getSaveFileName(parent, "保存结果", os.path.join(os.path.expanduser("~/Desktop"),
                                                                    default_name), "CSV (*.csv)")
    if not p:
        return
    arr = np.column_stack(columns)
    np.savetxt(p, arr, delimiter=",", header=",".join(header), comments="", fmt="%.6g", encoding="utf-8-sig")
    log(f"已保存 {p}")


# ====================================================================== 数据
class GroupDataPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "11. 多被试数据",
                         "对应手册第 3 章代码 2–4。每个条件添加所有被试的文件："
                         "可以是第 10 步导出的 -epo.fif 分段文件，也可以是手册用的 .mat / .h5 数组。")
        self.conds = QListWidget()
        self.conds.setMinimumHeight(120)
        self.conds.itemDoubleClicked.connect(lambda _: self.edit())
        self._items = []  # [(name, files, key)]
        self.add(box("条件", widgets=[
            self.conds,
            [button("添加条件…", self.new), button("编辑", self.edit), button("删除", self.delete), "stretch"],
            note("例：手册第 3 章有 First（首次看到熟悉面孔）和 Repetition（立即重复）两个条件，"
                 "各 8 个被试。")]))
        self.dim = combo(list(arrays.DIM_ORDERS))
        self.sfreq = fspin(250, 1, 100000, 50, 2, " Hz")
        self.tmin = fspin(-0.5, -60, 60, 0.1, 4, " s")
        self.add(box("数组文件的格式（-epo.fif 文件会自动读取，不用填）", rows=[
            ("维度顺序", self.dim), ("采样率", self.sfreq), ("分段起点时间", self.tmin)],
            widgets=[note("手册第 3 章：.mat 的 data 为 通道×时间×试次，250 Hz，从 −0.5 s 开始。")]))
        self.add(box("", widgets=[[button("读取全部数据", self.load, primary=True), "stretch"]]))
        self.info = info_box(100)
        self.add(box("数据概况", widgets=[self.info]))
        self.finish()

    def _refresh(self):
        self.conds.clear()
        for name, files, key in self._items:
            self.conds.addItem(f"{name}　←　{len(files)} 个文件" + (f"　[{key}]" if key else ""))

    def new(self):
        files = self._items[-1][1] if self._items else None
        d = ConditionDialog(self, files=files)
        if d.exec():
            self._items.append(d.value())
            self._refresh()

    def edit(self):
        r = self.conds.currentRow()
        if r < 0:
            return
        d = ConditionDialog(self, *self._items[r])
        if d.exec():
            self._items[r] = d.value()
            self._refresh()

    def delete(self):
        r = self.conds.currentRow()
        if r >= 0:
            del self._items[r]
            self._refresh()

    def load(self):
        if not self._items:
            QMessageBox.information(self, "条件", "请先添加至少一个条件。")
            return
        conds = [group.Condition(n, f, k) for n, f, k in self._items]
        dim = arrays.DIM_ORDERS[self.dim.currentText()]
        sf, t0 = self.sfreq.value(), self.tmin.value()

        def done(gd):
            self.state.group = gd
            self.state.group_result = {}
            self.info.setHtml(gd.summary())
            self.ctx.data_changed()

        self.run("读取多被试数据", lambda: group.load_group(conds, dim, sf, t0, log=log), done)

    def on_show(self):
        if self.state.group is not None:
            self.info.setHtml(self.state.group.summary())


# ====================================================================== 组 ERP
class GroupERPPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "12. 组 ERP 与统计检验",
                         "对应手册第 3 章代码 4–9，统计方法来自第 2 章代码 29–39："
                         "t 检验、置换检验、F 检验、重复测量方差分析，以及 Bonferroni / FDR / cluster 校正。")
        self.conds = _cond_list()
        self.add(box("参与比较的条件", widgets=[self.conds]))
        self.ch = line("50", "通道名（Cz）或从 1 开始的序号（50）；多个则取平均")
        self.w0 = fspin(-0.2, -60, 60, 0.1, 3, " s")
        self.w1 = fspin(1.0, -60, 60, 0.1, 3, " s")
        self.bl_on = check("基线校正", True)
        self.b0 = fspin(-0.1, -60, 60, 0.05, 3, " s")
        self.b1 = fspin(0.0, -60, 60, 0.05, 3, " s")
        self.ylim_on = check("固定 Y 轴范围")
        self.y0 = fspin(-10, -1e6, 1e6, 1, 2)
        self.y1 = fspin(10, -1e6, 1e6, 1, 2)
        self.add(box("ERP", rows=[("通道", self.ch), ("显示从", self.w0), ("到", self.w1), (None, self.bl_on),
                                  ("基线从", self.b0), ("到", self.b1), (None, self.ylim_on),
                                  ("Y 下限", self.y0), ("Y 上限", self.y1)],
                     widgets=[note("手册：第 50 个电极，−200 到 1000 ms，用 −100 到 0 ms 做基线。")]))
        self.test = combo([])
        self.corr = combo(stats.CORRECTIONS, "不校正")
        self.alpha = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.cp = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.niter = ispin(1000, 10, 100000, 100)
        self.levels = line("2, 2", "两因素各自的水平数，乘积 = 条件数")
        self.add(box("统计检验", rows=[("检验方法", self.test), ("多重比较校正", self.corr), ("p 阈值", self.alpha),
                                     ("cluster 水平 p", self.cp), ("置换次数", self.niter), ("因素水平", self.levels)],
                     widgets=[note("重复测量方差分析的条件顺序按 A1B1、A1B2、A2B1、A2B2 排列。"
                                   "cluster 方法自带多重比较校正，不再使用上面的校正选项。")]))
        self.each = check("每个条件另外单独画一张（手册代码 6、7）")
        self.add(box("", widgets=[self.each, [button("计算并画图", self.compute, primary=True),
                                              button("保存结果 CSV", self.save), "stretch"]]))
        self.conds.itemChanged.connect(lambda _: self._update_tests())
        self.finish()

    def on_show(self):
        gd = self.state.group
        _fill_conditions(self.conds, [c.name for c in gd.conditions] if gd else [])
        self._update_tests()

    def _update_tests(self):
        k = len(_selected_conditions(self.conds))
        items = stats.TESTS_1 if k == 1 else stats.TESTS_2 if k == 2 else stats.TESTS_N if k >= 3 else []
        cur = self.test.currentText()
        self.test.clear()
        self.test.addItem("不做检验")
        self.test.addItems(items)
        if self.test.findText(cur) >= 0:
            self.test.setCurrentText(cur)
        elif items:
            self.test.setCurrentIndex(1)

    def compute(self):
        gd = self.state.group
        if gd is None:
            QMessageBox.information(self, "没有数据", "请先在第 11 步读取多被试数据。")
            return
        names = _selected_conditions(self.conds)
        if not names:
            QMessageBox.information(self, "条件", "请至少勾选一个条件。")
            return
        try:
            ch = arrays.resolve_channels(self.ch.text(), gd.ch_names, gd.n_channels)
            levels = [int(x) for x in parse_floats(self.levels.text())]
        except ValueError as e:
            QMessageBox.warning(self, "参数", str(e))
            return
        win = (self.w0.value(), self.w1.value())
        bl = (self.b0.value(), self.b1.value()) if self.bl_on.isChecked() else None
        ylim = (self.y0.value(), self.y1.value()) if self.ylim_on.isChecked() else None
        test, corr = self.test.currentText(), self.corr.currentText()
        alpha, cp, niter, each = self.alpha.value(), self.cp.value(), self.niter.value(), self.each.isChecked()
        sub = group.GroupData([c for c in gd.conditions if c.name in names], gd.sfreq, gd.tmin,
                              gd.ch_names, gd.n_channels, gd.n_times)
        ch_label = self.ch.text().strip()

        def work():
            times, erps = group.group_erp(sub, ch, win, bl)
            res = None
            if test != "不做检验":
                res = stats.run_time_test(list(erps.values()), test, corr, alpha, niter,
                                          levels or None, cp, log=log)
            figs = []
            if each:
                for n, x in erps.items():
                    figs.append((group.plot_group_erp(times, {n: x}, None, ylim, f"{n}（通道 {ch_label}）"), n))
            title = f"通道 {ch_label}"
            if res:
                title += f" · {test}"
                if not test.startswith("cluster") and corr != "不校正":
                    title += f"（{corr} 校正）"
            figs.append((group.plot_group_erp(times, erps, res, ylim, title), "组 ERP"))
            return times, erps, res, figs

        def done(r):
            times, erps, res, figs = r
            self.state.group_result["erp"] = (times, erps, res)
            for f, t in figs:
                self.show_fig(f, t)

        self.run("组 ERP 与统计", work, done)

    def save(self):
        r = self.state.group_result.get("erp")
        if not r:
            QMessageBox.information(self, "没有结果", "请先计算。")
            return
        times, erps, res = r
        header, cols = ["time_s"], [times]
        for n, x in erps.items():
            header += [f"{n}_mean", f"{n}_sem"]
            cols += [x.mean(0), stats.sem(x)]
        if res:
            if "effects" in res:
                for n, e in res["effects"].items():
                    header += [f"{n}_F", f"{n}_p", f"{n}_p_corrected"]
                    cols += [e["stat"], e["p"], e["p_corr"]]
            else:
                if res.get("p") is not None:
                    header += ["stat", "p", "p_corrected"]
                    cols += [res["stat"], res["p"], res["p_corr"]]
                header.append("significant")
                cols.append(np.asarray(res["sig"], float))
        save_csv(self, header, cols, "组ERP结果.csv")


# ====================================================================== 组时频
class GroupTFRPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "13. 组时频分析",
                         "对应手册第 3 章代码 10–17：逐被试 Morlet 时频，dB 基线校正，"
                         "cluster 置换检验找显著的时频区域（红色虚线 > 0，蓝色虚线 < 0）。")
        self.conds = _cond_list()
        self.add(box("条件（勾 1 个或 2 个；2 个时另外画差异图）", widgets=[self.conds]))
        self.ch = line("50", "通道名或从 1 开始的序号；多个则取平均")
        self.f0 = fspin(4, 0.5, 500, 1, 1, " Hz")
        self.f1 = fspin(30, 1, 1000, 1, 1, " Hz")
        self.fs = fspin(2, 0.1, 100, 1, 2, " Hz")
        self.div = fspin(2, 0.1, 100, 0.5, 2)
        self.decim = ispin(1, 1, 50)
        self.b0 = fspin(-0.1, -60, 60, 0.05, 3, " s")
        self.b1 = fspin(0.0, -60, 60, 0.05, 3, " s")
        self.w0 = fspin(-0.2, -60, 60, 0.1, 3, " s")
        self.w1 = fspin(1.0, -60, 60, 0.1, 3, " s")
        self.add(box("时频", rows=[("通道", self.ch), ("频率从", self.f0), ("到", self.f1), ("步长", self.fs),
                                 ("n_cycles = 频率 /", self.div), ("降采样倍数", self.decim),
                                 ("基线从", self.b0), ("到", self.b1), ("显示从", self.w0), ("到", self.w1)],
                     widgets=[note("手册：4–30 Hz（步长 2），n_cycles = 频率/2，基线 −100 到 0 ms。")]))
        self.p = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.cp = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.niter = ispin(100, 10, 100000, 100)
        self.stat_on = check("做 cluster 置换检验", True)
        self.clim = fspin(3, 0.01, 100, 0.5, 2, " dB")
        self.dclim = fspin(2, 0.01, 100, 0.5, 2, " dB")
        self.add(box("统计与显示", rows=[(None, self.stat_on), ("p 阈值", self.p), ("cluster 水平 p", self.cp),
                                      ("置换次数", self.niter), ("色标范围 ±", self.clim), ("差异图色标 ±", self.dclim)],
                     widgets=[note("手册为了演示快用了 100 次置换，正式分析建议 1000 次以上（会更慢）。"),
                              [button("计算并画图", self.compute, primary=True), button("保存结果 .npz", self.save),
                               "stretch"]]))
        self.finish()

    def on_show(self):
        gd = self.state.group
        _fill_conditions(self.conds, [c.name for c in gd.conditions] if gd else [])

    def compute(self):
        gd = self.state.group
        if gd is None:
            QMessageBox.information(self, "没有数据", "请先在第 11 步读取多被试数据。")
            return
        names = _selected_conditions(self.conds)
        if len(names) not in (1, 2):
            QMessageBox.information(self, "条件", "请勾选 1 个或 2 个条件。")
            return
        try:
            ch = arrays.resolve_channels(self.ch.text(), gd.ch_names, gd.n_channels)
        except ValueError as e:
            QMessageBox.warning(self, "通道", str(e))
            return
        freqs = np.arange(self.f0.value(), self.f1.value() + self.fs.value() / 2, self.fs.value())
        sub = group.GroupData([c for c in gd.conditions if c.name in names], gd.sfreq, gd.tmin,
                              gd.ch_names, gd.n_channels, gd.n_times)
        args = (ch, freqs, self.div.value(), (self.b0.value(), self.b1.value()),
                (self.w0.value(), self.w1.value()), self.decim.value())
        do_stat, p, cp, niter = self.stat_on.isChecked(), self.p.value(), self.cp.value(), self.niter.value()
        clim, dclim = self.clim.value(), self.dclim.value()
        ch_label = self.ch.text().strip()

        def work():
            fr, times, tfrs = group.group_tfr(sub, *args, log=log)
            figs, masks = [], {}
            for n, x in tfrs.items():
                m = group.tfr_cluster(x, p, cp, niter) if do_stat else None
                masks[n] = m
                figs.append((group.plot_tfr_map(x.mean(0), fr, times, m, (-clim, clim),
                                                f"{n}（通道 {ch_label}）"), f"时频 {n}"))
            if len(tfrs) == 2:
                (n1, a), (n2, b) = tfrs.items()
                m = group.tfr_cluster(a, p, cp, niter, other=b) if do_stat else None
                masks["diff"] = m
                figs.append((group.plot_tfr_map((a - b).mean(0), fr, times, m, (-dclim, dclim),
                                                f"{n1} − {n2}（通道 {ch_label}）", cbar="ΔdB"), "时频差异"))
            return fr, times, tfrs, masks, figs

        def done(r):
            fr, times, tfrs, masks, figs = r
            self.state.group_result["tfr"] = (fr, times, tfrs, masks)
            for f, t in figs:
                self.show_fig(f, t)

        self.run("组时频分析", work, done)

    def save(self):
        r = self.state.group_result.get("tfr")
        if not r:
            QMessageBox.information(self, "没有结果", "请先计算。")
            return
        fr, times, tfrs, masks = r
        p, _ = QFileDialog.getSaveFileName(self, "保存结果", os.path.join(os.path.expanduser("~/Desktop"),
                                                                       "组时频结果.npz"), "NumPy (*.npz)")
        if not p:
            return
        out = dict(freqs=fr, times=times)
        for n, x in tfrs.items():
            out[f"tfr_{n}"] = x
        for n, m in masks.items():
            if m is not None:
                out[f"mask_{n}"] = m
        np.savez(p, **out)
        log(f"已保存 {p}（tfr_* 形状为 被试×频率×时间）")
