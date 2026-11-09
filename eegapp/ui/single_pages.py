"""第 1 章各步骤页面。"""
import os

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHeaderView,
                               QListWidget, QListWidgetItem, QMessageBox, QTableWidget,
                               QTableWidgetItem)

from ..compat import resource_path
from ..core import reading, single
from .common import (Page, button, check, combo, fspin, group, info_box, ispin, line, log, note)


def _checked_items(lw):
    return [lw.item(i).data(Qt.UserRole) for i in range(lw.count())
            if lw.item(i).checkState() == Qt.Checked]


def _fill_checklist(lw, items, checked):
    lw.clear()
    for value, text in items:
        it = QListWidgetItem(text)
        it.setData(Qt.UserRole, value)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if value in checked else Qt.Unchecked)
        lw.addItem(it)


def _browse_line(parent, edit, title, filt, save=False, folder=False):
    def go():
        start = edit.text() or os.path.expanduser("~/Desktop")
        if folder:
            p = QFileDialog.getExistingDirectory(parent, title, start)
        elif save:
            p, _ = QFileDialog.getSaveFileName(parent, title, start, filt)
        else:
            p, _ = QFileDialog.getOpenFileName(parent, title, start, filt)
        if p:
            edit.setText(os.path.normpath(p))
    return button("浏览…", go)


# ====================================================================== 1 读取
class ReadPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "1. 读取数据",
                         "选择一个连续脑电文件，格式自动识别（EDF、BDF、BrainVision、EEGLAB、"
                         "Neuroscan CNT、GDF、FIF 等）。中文注释的 EDF/BDF 会自动换 GBK 编码重试；"
                         "BDF 等只有触发通道的数据，会从触发通道提取事件。")
        self.path = line(placeholder="例如 D:\\数据\\被试01.vhdr")
        self.event_map = line(placeholder="可不填。例：1=rt, 2=square")
        self.add(group("数据文件", rows=[
            ("文件", self.path),
            (None, _browse_line(self, self.path, "选择脑电文件", reading.RAW_FILE_FILTER)),
            ("事件码对照", self.event_map),
        ], widgets=[
            note("CNT、GDF、BrainVision 等格式只存数字事件码，可以在“事件码对照”里给数字起名字。"),
            [button("读取", self.load, primary=True), button("打开手册示例数据", self.load_sample),
             "stretch"],
        ]))
        self.info = info_box(180)
        self.add(group("数据概况", widgets=[self.info]))
        self.add(group("撤销", widgets=[
            note("滤波、ICA 等操作会直接修改数据。需要从头再来时，可以恢复到刚读取时的状态。"),
            [button("恢复到刚读取的数据", self.restore), "stretch"]]))
        self.finish()

    def load_sample(self):
        p = resource_path("sample_data", "eeglab_data.set")
        if not os.path.exists(p):
            QMessageBox.information(self, "没有示例数据", f"找不到 {p}")
            return
        self.path.setText(p)
        self.event_map.setText("")
        self.load()

    def load(self):
        path = self.path.text().strip()
        if not path:
            QMessageBox.information(self, "请选择文件", "先选择一个脑电文件。")
            return
        try:
            emap = reading.parse_event_map(self.event_map.text())
        except ValueError as e:
            QMessageBox.warning(self, "事件码对照", str(e))
            return

        def work():
            raw, p = reading.read_raw_any(path, emap, log=log)
            return raw, p, raw.copy()

        def done(res):
            st = self.state
            st.raw, st.raw_path, st.raw_orig = res
            st.reset_after_raw()
            st.history = []
            st.record(f"读取 {st.raw_path}")
            self.info.setHtml(reading.describe_raw(st.raw, st.raw_path))
            self.ctx.data_changed()

        self.run("读取数据", work, done)

    def restore(self):
        st = self.state
        if st.raw_orig is None:
            return
        st.raw = st.raw_orig.copy()
        st.reset_after_raw()
        st.record("恢复到刚读取的数据")
        log("已恢复到刚读取的数据")
        self.ctx.data_changed()

    def on_show(self):
        if self.state.raw is not None:
            self.info.setHtml(reading.describe_raw(self.state.raw, self.state.raw_path))


# ====================================================================== 2 通道与电极
class ChannelPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "2. 通道与电极定位",
                         "对应手册单元 4–6、9：导入电极位置、修正通道名、设置 EOG 等通道类型。"
                         "画地形图、插值坏导、ICA 地形图都需要电极位置。")
        self.mode = combo(["不改变", "标准模板", "位置文件"])
        self.std = combo(single.STANDARD_MONTAGES, "standard_1020")
        self.locs = line(placeholder=".locs / .elc / .sfp / .loc / .txt …")
        self.rename = check("用位置文件里的通道名依次替换数据里的 EEG 通道名（手册单元 4 的做法）")
        self.add(group("电极位置", rows=[
            ("方式", self.mode), ("标准模板", self.std), ("位置文件", self.locs),
            (None, _browse_line(self, self.locs, "选择电极位置文件",
                                "电极位置 (*.locs *.loc *.elc *.sfp *.elp *.txt *.csd *.bvef *.hpts);;所有文件 (*)")),
            (None, self.rename)]))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["通道", "类型"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(220)
        self.add(group("通道类型（默认都是 eeg，眼电改成 eog）", widgets=[
            self.table, [button("名字含 EOG / ECG / EMG 的自动设类型", self.auto_types), "stretch"],
            note("表格按通道顺序逐行对应，同时勾选了重命名时，类型会按行套到新通道名上。")]))

        self.reref = combo(["不改变", "平均参考", "指定通道"])
        self.reref_ch = line(placeholder="例：M1, M2")
        self.add(group("重参考（可选，手册未做）", rows=[("方式", self.reref), ("参考通道", self.reref_ch)]))
        self.add(group("", widgets=[[button("应用以上设置", self.apply, primary=True),
                                     button("电极位置图", self.sensors), "stretch"]]))
        self.finish()

    def on_show(self):
        raw = self.state.raw
        self.table.setRowCount(0)
        if raw is None:
            return
        types = raw.get_channel_types()
        self.table.setRowCount(len(raw.ch_names))
        for i, (ch, t) in enumerate(zip(raw.ch_names, types)):
            it = QTableWidgetItem(ch)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, it)
            cb = QComboBox()
            cb.addItems(single.CHANNEL_TYPES if t in single.CHANNEL_TYPES else single.CHANNEL_TYPES + [t])
            cb.setCurrentText(t)
            self.table.setCellWidget(i, 1, cb)

    def auto_types(self):
        n = 0
        for i in range(self.table.rowCount()):
            name = self.table.item(i, 0).text().upper()
            for key in ("EOG", "ECG", "EMG"):
                if key in name:
                    self.table.cellWidget(i, 1).setCurrentText(key.lower())
                    n += 1
        log(f"按通道名自动设置了 {n} 个通道的类型（点“应用以上设置”后生效）")

    def apply(self):
        if not self.need("raw", "请先读取数据。"):
            return
        mode = {"不改变": None, "标准模板": "standard", "位置文件": "file"}[self.mode.currentText()]
        value = self.std.currentText() if mode == "standard" else self.locs.text().strip()
        if mode == "file" and not value:
            QMessageBox.information(self, "位置文件", "请选择电极位置文件。")
            return
        types = [self.table.cellWidget(i, 1).currentText() for i in range(self.table.rowCount())]
        raw = self.state.raw
        reref, reref_ch, rename = self.reref.currentText(), self.reref_ch.text(), self.rename.isChecked()

        def work():
            single.apply_channel_setup(raw, mode, value, rename, types, reref, reref_ch, log=log)

        def done(_):
            self.state.record(f"通道设置：电极={mode}:{value}，重参考={reref}")
            self.on_show()
            self.ctx.data_changed()

        self.run("通道与电极设置", work, done)

    def sensors(self):
        if not self.need("raw", "请先读取数据。"):
            return
        if self.state.raw.get_montage() is None:
            QMessageBox.information(self, "没有电极位置", "请先设置电极位置。")
            return
        raw = self.state.raw
        self.run("电极位置图", lambda: single.plot_sensors(raw), lambda f: self.show_fig(f, "电极位置"))


# ====================================================================== 3 查看
class ViewPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "3. 查看波形与频谱",
                         "对应手册单元 7、8、10。波形浏览器里点击通道名可以把它标为坏导，"
                         "关闭浏览器后生效；也可以拖动鼠标添加 BAD 标注。")
        self.dur = fspin(5, 0.5, 600, 1, 1, " s")
        self.nch = ispin(32, 1, 512)
        self.add(group("波形浏览器", rows=[("每屏时长", self.dur), ("每屏通道数", self.nch)],
                       widgets=[[button("打开波形浏览器", self.browse, primary=True), "stretch"]]))
        self.fmin = fspin(0, 0, 5000, 1, 1, " Hz")
        self.fmax = fspin(80, 1, 5000, 1, 1, " Hz")
        self.avg = check("所有通道平均成一条曲线", True)
        self.add(group("功率谱密度 (PSD)", rows=[("最低频率", self.fmin), ("最高频率", self.fmax),
                                               (None, self.avg)],
                       widgets=[[button("功率谱", self.psd), button("功率谱拓扑图", self.topo), "stretch"]]))
        self.finish()

    def browse(self):
        if not self.need("raw", "请先读取数据。"):
            return
        raw = self.state.raw
        try:
            b = raw.plot(duration=self.dur.value(), n_channels=self.nch.value(), clipping=None,
                         block=False, show=True, title=str(self.state.raw_path or "波形"))
            self.ctx.keep_window(b, on_close=self.ctx.data_changed)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "波形浏览器", str(e))

    def psd(self):
        if not self.need("raw", "请先读取数据。"):
            return
        raw, a, b, avg = self.state.raw, self.fmin.value(), self.fmax.value(), self.avg.isChecked()
        self.run("计算功率谱", lambda: single.plot_psd(raw, a, b, avg), lambda f: self.show_fig(f, "功率谱"))

    def topo(self):
        if not self.need("raw", "请先读取数据。"):
            return
        if self.state.raw.get_montage() is None:
            QMessageBox.information(self, "没有电极位置", "拓扑图需要电极位置，请先在第 2 步设置。")
            return
        raw, a, b = self.state.raw, self.fmin.value(), self.fmax.value()
        self.run("功率谱拓扑图", lambda: single.plot_psd_topo(raw, a, b), lambda f: self.show_fig(f, "PSD 拓扑"))


# ====================================================================== 4 滤波
class FilterPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "4. 滤波",
                         "对应手册单元 11–14。手册示例数据来自美国，工频是 60 Hz；"
                         "国内采集的数据工频是 50 Hz。")
        self.notch_on = check("陷波滤波（去工频干扰）", True)
        self.notch = fspin(50, 1, 1000, 10, 1, " Hz")
        self.harm = check("同时去除谐波（100、150 Hz…）")
        self.bp_on = check("带通滤波", True)
        self.lf = fspin(0.1, 0, 1000, 0.1, 2, " Hz")
        self.hf = fspin(30, 0, 5000, 1, 1, " Hz")
        self.method = combo(["fir", "iir"])
        self.rs_on = check("重采样")
        self.rs = fspin(250, 10, 20000, 50, 1, " Hz")
        self.show_psd = check("完成后显示功率谱", True)
        self.add(group("参数", rows=[
            (None, self.notch_on), ("陷波频率", self.notch), (None, self.harm),
            (None, self.bp_on), ("高通（下限）", self.lf), ("低通（上限）", self.hf),
            ("滤波方法", self.method), (None, self.rs_on), ("新采样率", self.rs),
            (None, self.show_psd)],
            widgets=[note("高通填 0 表示不做高通，低通填 0 表示不做低通。"),
                     [button("应用滤波", self.apply, primary=True), "stretch"]]))
        self.finish()

    def apply(self):
        if not self.need("raw", "请先读取数据。"):
            return
        raw = self.state.raw
        notch = self.notch.value() if self.notch_on.isChecked() else None
        lf = single.none_if_zero(self.lf.value()) if self.bp_on.isChecked() else None
        hf = single.none_if_zero(self.hf.value()) if self.bp_on.isChecked() else None
        rs = self.rs.value() if self.rs_on.isChecked() else None
        harm, method, show = self.harm.isChecked(), self.method.currentText(), self.show_psd.isChecked()

        def work():
            single.apply_filters(raw, notch, harm, lf, hf, method, rs, log=log)
            return single.plot_psd(raw, 0, max((hf or 40) * 2, 60)) if show else None

        def done(fig):
            self.state.record(f"滤波：陷波={notch}，带通={lf}–{hf}（{method}），重采样={rs}")
            self.state.reset_after_raw()
            self.show_fig(fig, "滤波后功率谱")
            self.ctx.data_changed()

        self.run("滤波", work, done)


# ====================================================================== 5 坏导
class BadsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "5. 坏导标记与插值",
                         "对应手册单元 15–16。勾选坏导后点“标记”，再“插值”用周围电极重建它们。"
                         "也可以在波形浏览器里点击通道名标记。")
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        self.add(group("EEG 通道（勾选 = 坏导）", widgets=[
            self.list, [button("标记坏导", self.mark, primary=True),
                        button("插值坏导", self.interp), button("刷新", self.on_show), "stretch"]]))
        self.finish()

    def on_show(self):
        raw = self.state.raw
        if raw is None:
            self.list.clear()
            return
        eeg = [ch for ch, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eeg"]
        _fill_checklist(self.list, [(c, c) for c in eeg], set(raw.info["bads"]))

    def mark(self):
        if not self.need("raw", "请先读取数据。"):
            return
        bads = _checked_items(self.list)
        single.set_bads(self.state.raw, bads, log=log)
        self.state.record(f"坏导：{bads}")
        self.ctx.data_changed()

    def interp(self):
        if not self.need("raw", "请先读取数据。"):
            return
        self.mark()
        raw = self.state.raw

        def done(_):
            self.state.record("插值坏导")
            self.state.reset_after_raw()
            self.on_show()
            self.ctx.data_changed()

        self.run("插值坏导", lambda: single.interpolate_bads(raw, log=log), done)


# ====================================================================== 6 ICA
class ICAPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "6. ICA 去除伪迹",
                         "对应手册单元 17–22。先拟合 ICA，再看成分波形和地形图，找出眼动、心电等伪迹成分，"
                         "填到“排除成分”里，确认后应用。拟合时会在数据副本上做 1 Hz 高通，原数据不受影响。")
        self.ncomp = line("", "留空 = 全部；整数 = 成分数；小数 = 解释方差比例，如 0.99")
        self.method = combo(["fastica", "infomax", "picard"])
        self.hp = fspin(1.0, 0, 10, 0.5, 1, " Hz")
        self.seed = ispin(97, 0, 100000)
        self.add(group("拟合", rows=[("成分数", self.ncomp), ("算法", self.method),
                                    ("拟合前高通", self.hp), ("随机种子", self.seed)],
                       widgets=[[button("拟合 ICA", self.fit, primary=True), "stretch"]]))
        self.exclude = line("", "例：0, 1（成分编号从 0 开始）")
        self.add(group("检查与排除", rows=[("排除成分", self.exclude)], widgets=[
            [button("成分波形浏览器", self.sources), button("成分地形图", self.components)],
            [button("自动识别眼电成分", self.find_eog), button("叠加对比", self.overlay)],
            [button("成分属性", self.properties), "stretch"],
            note("在成分波形浏览器里点击成分名，也能把它加入排除列表。"),
            [button("应用 ICA（去除所选成分）", self.apply, primary=True), "stretch"]]))
        self.finish()

    def on_show(self):
        ica = self.state.ica
        if ica is not None and ica.exclude and not self.exclude.text().strip():
            self.exclude.setText(", ".join(map(str, ica.exclude)))

    def _ncomp(self):
        t = self.ncomp.text().strip()
        if not t:
            return None
        return float(t) if "." in t else int(t)

    def _exclude(self):
        t = self.exclude.text().strip()
        if not t and self.state.ica is not None:
            return list(self.state.ica.exclude)
        return [int(x) for x in single.parse_floats(t)]

    def fit(self):
        if not self.need("raw", "请先读取数据。"):
            return
        try:
            n = self._ncomp()
        except ValueError:
            QMessageBox.warning(self, "成分数", "成分数要填整数或小数。")
            return
        raw, method, hp, seed = self.state.raw, self.method.currentText(), self.hp.value(), self.seed.value()

        def done(res):
            self.state.ica, self.state.raw_for_ica = res
            self.state.record(f"ICA 拟合：{method}，成分={n}")
            self.ctx.data_changed()

        self.run("拟合 ICA", lambda: single.fit_ica(raw, n, method, hp, seed, log=log), done)

    def _need_ica(self):
        return self.need("ica", "请先拟合 ICA。")

    def sources(self):
        if not self._need_ica():
            return
        ica = self.state.ica
        ica.exclude = self._exclude()
        try:
            b = ica.plot_sources(self.state.raw_for_ica, block=False, show=True)
            self.ctx.keep_window(b, on_close=lambda: self.exclude.setText(
                ", ".join(map(str, ica.exclude))))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "成分波形", str(e))

    def components(self):
        if not self._need_ica():
            return
        if self.state.raw.get_montage() is None:
            QMessageBox.information(self, "没有电极位置", "成分地形图需要电极位置。")
            return
        ica = self.state.ica
        self.run("成分地形图", lambda: ica.plot_components(show=False),
                 lambda f: self.show_fig(f, "ICA 地形图"))

    def find_eog(self):
        if not self._need_ica():
            return
        ica, raw = self.state.ica, self.state.raw

        def done(idx):
            self.exclude.setText(", ".join(map(str, idx)))
        self.run("识别眼电成分", lambda: single.find_eog_components(ica, raw, log=log), done)

    def overlay(self):
        if not self._need_ica():
            return
        ica, r, ex = self.state.ica, self.state.raw_for_ica, self._exclude()
        if not ex:
            QMessageBox.information(self, "排除成分", "请先填写要排除的成分。")
            return
        self.run("叠加对比", lambda: ica.plot_overlay(r, exclude=ex, show=False),
                 lambda f: self.show_fig(f, f"ICA 叠加对比 {ex}"))

    def properties(self):
        if not self._need_ica():
            return
        ica, raw, ex = self.state.ica, self.state.raw, self._exclude()
        if not ex:
            QMessageBox.information(self, "排除成分", "请先填写要查看的成分。")
            return
        self.run("成分属性", lambda: ica.plot_properties(raw, picks=ex, show=False),
                 lambda f: self.show_fig(f, "成分属性"))

    def apply(self):
        if not self._need_ica():
            return
        ex = self._exclude()
        if not ex:
            QMessageBox.information(self, "排除成分", "请先填写要排除的成分。")
            return
        ica, raw = self.state.ica, self.state.raw

        def done(_):
            self.state.record(f"应用 ICA，排除成分 {ex}")
            self.state.epochs = None
            self.state.power = self.state.itc = None
            self.ctx.data_changed()

        self.run("应用 ICA", lambda: single.apply_ica(ica, raw, ex, log=log), done)


# ====================================================================== 7 分段
class EpochPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "7. 事件与分段",
                         "对应手册单元 24–34。按事件把连续数据切成试次（epochs），做基线校正，"
                         "剔除振幅超过阈值的试次。")
        self.events = QListWidget()
        self.events.setMinimumHeight(130)
        self.add(group("事件（勾选要分段的事件）", widgets=[self.events]))
        self.tmin = fspin(-1.0, -60, 60, 0.1, 3, " s")
        self.tmax = fspin(2.0, -60, 60, 0.1, 3, " s")
        self.bl_on = check("基线校正", True)
        self.bl0 = fspin(-0.5, -60, 60, 0.1, 3, " s")
        self.bl1 = fspin(0.0, -60, 60, 0.1, 3, " s")
        self.reject = fspin(200, 0, 1e5, 10, 1, " µV")
        self.flat = fspin(0, 0, 1e5, 0.1, 2, " µV")
        self.rba = check("剔除与 BAD 标注重叠的试次", True)
        self.add(group("分段参数", rows=[
            ("开始", self.tmin), ("结束", self.tmax), (None, self.bl_on),
            ("基线开始", self.bl0), ("基线结束", self.bl1),
            ("峰峰值剔除阈值", self.reject), ("平坦信号阈值", self.flat), (None, self.rba)],
            widgets=[note("阈值填 0 表示不剔除。手册用 2e-4 V，即 200 µV。"),
                     [button("创建分段", self.make, primary=True), "stretch"]]))
        self.summary = info_box(90)
        self.add(group("分段结果", widgets=[self.summary]))
        self.bands = line("Theta:4-8, Alpha:8-12, Beta:12-30")
        self.add(group("查看分段", rows=[("频段", self.bands)], widgets=[
            [button("分段浏览器", self.browse), button("分段功率谱", self.psd)],
            [button("频段功率地形图", self.band_topo), "stretch"]]))
        self.finish()

    def on_show(self):
        raw = self.state.raw
        if raw is None:
            self.events.clear()
            return
        prev = set(_checked_items(self.events))
        tab = single.event_table(raw)
        names = [n for n, _ in tab]
        checked = prev & set(names) or set(names)
        _fill_checklist(self.events, [(n, f"{n}　（{c} 个）") for n, c in tab], checked)
        if self.state.epochs is not None:
            self.summary.setHtml(single.epochs_summary(self.state.epochs))

    def make(self):
        if not self.need("raw", "请先读取数据。"):
            return
        names = _checked_items(self.events)
        if not names:
            QMessageBox.information(self, "事件", "数据里没有事件，或者没有勾选事件。")
            return
        raw = self.state.raw
        bl = (self.bl0.value(), self.bl1.value()) if self.bl_on.isChecked() else None
        args = (names, self.tmin.value(), self.tmax.value(), bl,
                single.none_if_zero(self.reject.value()), single.none_if_zero(self.flat.value()),
                self.rba.isChecked())

        def done(ep):
            self.state.epochs = ep
            self.state.reset_after_epochs()
            self.state.record(f"分段：事件={names}，{args[1]}–{args[2]} s，基线={bl}，剔除={args[4]} µV")
            self.summary.setHtml(single.epochs_summary(ep))
            self.ctx.data_changed()

        self.run("创建分段", lambda: single.make_epochs(raw, *args, log=log), done)

    def browse(self):
        if not self.need("epochs", "请先创建分段。"):
            return
        try:
            b = self.state.epochs.plot(n_epochs=4, block=False, show=True)
            self.ctx.keep_window(b, on_close=lambda: self.summary.setHtml(
                single.epochs_summary(self.state.epochs)))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "分段浏览器", str(e))

    def psd(self):
        if not self.need("epochs", "请先创建分段。"):
            return
        ep = self.state.epochs
        self.run("分段功率谱", lambda: single.plot_epochs_psd(ep, 0, 60), lambda f: self.show_fig(f, "分段 PSD"))

    def band_topo(self):
        if not self.need("epochs", "请先创建分段。"):
            return
        try:
            bands = single.parse_bands(self.bands.text())
        except ValueError as e:
            QMessageBox.warning(self, "频段", str(e))
            return
        ep = self.state.epochs
        self.run("频段功率地形图", lambda: single.plot_band_topomap(ep, bands),
                 lambda f: self.show_fig(f, "频段地形图"))


def _condition_combo():
    return combo(["全部条件"])


def _refresh_conditions(cb, epochs):
    cur = cb.currentText()
    cb.clear()
    cb.addItem("全部条件")
    if epochs is not None:
        cb.addItems(list(epochs.event_id.keys()))
    if cb.findText(cur) >= 0:
        cb.setCurrentText(cur)


# ====================================================================== 8 ERP
class ERPPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "8. ERP（叠加平均）",
                         "对应手册单元 35–43。把同一条件的试次平均成 ERP，画波形、地形图、联合图等。")
        self.cond = _condition_combo()
        self.add(group("条件", rows=[("条件", self.cond)]))
        self.times = line("0, 0.5, 1, 1.5, 2", "留空或写 auto = 自动选峰值时刻")
        self.avgwin = fspin(0, 0, 5, 0.05, 3, " s")
        self.add(group("波形与地形图", rows=[("地形图时刻 (s)", self.times), ("时刻附近平均", self.avgwin)],
                       widgets=[[button("ERP 波形", self.wave, primary=True), button("地形图", self.topomap)],
                                [button("联合图", self.joint), button("图像", self.image)],
                                [button("拓扑排布", self.topo), "stretch"],
                                note("手册单元 38 在 0.8 s 取 0.75–0.85 s 的平均：时刻填 0.8，时刻附近平均填 0.1。")]))
        self.picks = line("O1, Oz, O2", "留空 = 所有 EEG 通道")
        self.add(group("条件比较", rows=[("通道（取平均）", self.picks)],
                       widgets=[[button("比较各条件 ERP", self.compare), "stretch"]]))
        self.finish()

    def on_show(self):
        _refresh_conditions(self.cond, self.state.epochs)

    def _evoked(self):
        if not self.need("epochs", "请先在第 7 步创建分段。"):
            return None
        return single.make_evoked(self.state.epochs, self.cond.currentText())

    def _need_montage(self):
        if self.state.epochs.get_montage() is None:
            QMessageBox.information(self, "没有电极位置", "这张图需要电极位置。")
            return False
        return True

    def wave(self):
        evk = self._evoked()
        if evk is not None:
            self.run("ERP 波形", lambda: evk.plot(show=False, spatial_colors=evk.get_montage() is not None),
                     lambda f: self.show_fig(f, f"ERP {evk.comment}"))

    def topomap(self):
        evk = self._evoked()
        if evk is None or not self._need_montage():
            return
        t = self.times.text().strip()
        times = "auto" if t in ("", "auto") else single.parse_floats(t)
        if times != "auto":
            times = [x for x in times if evk.times[0] <= x <= evk.times[-1]]
        avg = single.none_if_zero(self.avgwin.value())
        self.run("ERP 地形图", lambda: evk.plot_topomap(times=times, average=avg, colorbar=True, show=False),
                 lambda f: self.show_fig(f, f"地形图 {evk.comment}"))

    def joint(self):
        evk = self._evoked()
        if evk is None or not self._need_montage():
            return
        self.run("ERP 联合图", lambda: evk.plot_joint(show=False), lambda f: self.show_fig(f, "联合图"))

    def image(self):
        evk = self._evoked()
        if evk is not None:
            self.run("ERP 图像", lambda: evk.plot_image(show=False), lambda f: self.show_fig(f, "ERP 图像"))

    def topo(self):
        evk = self._evoked()
        if evk is None or not self._need_montage():
            return
        self.run("ERP 拓扑排布", lambda: evk.plot_topo(show=False), lambda f: self.show_fig(f, "ERP 拓扑"))

    def compare(self):
        if not self.need("epochs", "请先在第 7 步创建分段。"):
            return
        ep = self.state.epochs
        picks = single.parse_names(self.picks.text())
        missing = [p for p in picks if p not in ep.ch_names]
        if missing:
            QMessageBox.warning(self, "通道", f"找不到通道：{', '.join(missing)}")
            return
        conds = list(ep.event_id.keys())
        self.run("条件比较", lambda: single.plot_compare(ep, conds, picks or None),
                 lambda f: self.show_fig(f, "条件比较"))


# ====================================================================== 9 时频
class TFRPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "9. 时频分析",
                         "对应手册单元 44–50。Morlet 小波计算诱发功率（power）和试次间相位一致性（ITC）。")
        self.cond = _condition_combo()
        self.fmin = fspin(4, 0.5, 500, 1, 1, " Hz")
        self.fmax = fspin(30, 1, 1000, 1, 1, " Hz")
        self.nf = ispin(10, 2, 200)
        self.div = fspin(2, 0.1, 100, 0.5, 2)
        self.logsp = check("对数间隔的频率", True)
        self.decim = ispin(1, 1, 50)
        self.add(group("计算", rows=[("条件", self.cond), ("最低频率", self.fmin), ("最高频率", self.fmax),
                                    ("频率个数", self.nf), ("n_cycles = 频率 /", self.div), (None, self.logsp),
                                    ("降采样倍数", self.decim)],
                       widgets=[[button("计算时频", self.compute, primary=True), "stretch"]]))
        self.picks = line("O1, Oz, O2")
        self.combine = check("所选通道合并成一张（取平均）")
        self.bl0 = fspin(-0.5, -60, 60, 0.1, 3, " s")
        self.bl1 = fspin(0, -60, 60, 0.1, 3, " s")
        self.mode = combo(["logratio", "mean", "ratio", "percent", "zscore", "zlogratio"])
        self.add(group("出图参数", rows=[("通道", self.picks), (None, self.combine), ("基线开始", self.bl0),
                                      ("基线结束", self.bl1), ("基线方式", self.mode)],
                       widgets=[[button("通道时频图", self.plot_ch), button("拓扑排布", self.plot_topo)],
                                [button("ITC 拓扑排布", self.plot_itc), "stretch"]]))
        self.t0 = fspin(0, -60, 60, 0.1, 3, " s")
        self.t1 = fspin(0.5, -60, 60, 0.1, 3, " s")
        self.f0 = fspin(4, 0, 1000, 1, 1, " Hz")
        self.f1 = fspin(8, 0, 1000, 1, 1, " Hz")
        self.add(group("频段地形图", rows=[("时间从", self.t0), ("到", self.t1), ("频率从", self.f0), ("到", self.f1)],
                       widgets=[[button("频段地形图", self.plot_topomap), "stretch"]]))
        self.tf = line("0.5:10, 1:8", "时刻:频率，逗号分隔")
        self.jt0 = fspin(-0.5, -60, 60, 0.1, 3, " s")
        self.jt1 = fspin(1.5, -60, 60, 0.1, 3, " s")
        self.add(group("联合图", rows=[("时刻:频率", self.tf), ("显示从", self.jt0), ("到", self.jt1)],
                       widgets=[[button("时频联合图", self.plot_joint), "stretch"]]))
        self.finish()

    def on_show(self):
        _refresh_conditions(self.cond, self.state.epochs)

    def compute(self):
        if not self.need("epochs", "请先在第 7 步创建分段。"):
            return
        ep = single.select_epochs(self.state.epochs, self.cond.currentText())
        cond = self.cond.currentText()
        args = (self.fmin.value(), self.fmax.value(), self.nf.value(), self.div.value(),
                self.logsp.isChecked(), self.decim.value())

        def done(res):
            self.state.power, self.state.itc = res
            self.state.tfr_condition = cond
            self.state.record(f"时频分析：条件={cond}，{args[0]}–{args[1]} Hz，{args[2]} 个频率")
            self.ctx.data_changed()

        self.run("时频分析", lambda: single.compute_tfr(ep, *args, log=log), done)

    def _need(self):
        return self.need("power", "请先计算时频。")

    def _bl(self):
        return (self.bl0.value(), self.bl1.value())

    def plot_ch(self):
        if not self._need():
            return
        pw = self.state.power
        picks = single.parse_names(self.picks.text()) or "eeg"
        if isinstance(picks, list):
            missing = [p for p in picks if p not in pw.ch_names]
            if missing:
                QMessageBox.warning(self, "通道", f"找不到通道：{', '.join(missing)}")
                return
        comb = "mean" if self.combine.isChecked() else None
        bl, mode = self._bl(), self.mode.currentText()
        self.run("通道时频图", lambda: pw.plot(picks=picks, baseline=bl, mode=mode, combine=comb,
                                          title="auto" if comb is None else "所选通道平均", show=False),
                 lambda f: self.show_fig(f, "时频图"))

    def plot_topo(self):
        if not self._need():
            return
        pw, bl, mode = self.state.power, self._bl(), self.mode.currentText()
        self.run("时频拓扑", lambda: pw.plot_topo(baseline=bl, mode=mode, title="Average power", show=False),
                 lambda f: self.show_fig(f, "时频拓扑"))

    def plot_itc(self):
        if not self._need():
            return
        itc, bl, mode = self.state.itc, self._bl(), self.mode.currentText()
        self.run("ITC 拓扑", lambda: itc.plot_topo(baseline=bl, mode=mode, title="Inter-Trial coherence",
                                                   show=False),
                 lambda f: self.show_fig(f, "ITC 拓扑"))

    def plot_topomap(self):
        if not self._need():
            return
        pw, bl, mode = self.state.power, self._bl(), self.mode.currentText()
        a = dict(tmin=self.t0.value(), tmax=self.t1.value(), fmin=self.f0.value(), fmax=self.f1.value())
        self.run("时频地形图", lambda: pw.plot_topomap(baseline=bl, mode=mode, show=False, **a),
                 lambda f: self.show_fig(f, f"{a['fmin']:g}–{a['fmax']:g} Hz 地形图"))

    def plot_joint(self):
        if not self._need():
            return
        try:
            tfs = []
            for part in single.parse_names(self.tf.text()):
                t, f = part.split(":")
                tfs.append((float(t), float(f)))
        except ValueError:
            QMessageBox.warning(self, "时刻:频率", "写法例子：0.5:10, 1:8")
            return
        pw, bl = self.state.power, self._bl()
        a, b = self.jt0.value(), self.jt1.value()
        self.run("时频联合图", lambda: pw.plot_joint(baseline=bl, mode="mean", tmin=a, tmax=b,
                                                timefreqs=tfs or None, show=False),
                 lambda f: self.show_fig(f, "时频联合图"))


# ====================================================================== 10 导出
class ExportPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "10. 导出结果",
                         "对应手册单元 51–54。导出的 -epo.fif 分段文件可以直接用于第 3、4 章的多被试分析。"
                         "图片请用每张图上方工具栏的保存按钮。")
        self.folder = line(os.path.expanduser("~/Desktop"))
        self.name = line("", "留空 = 用原文件名")
        self.c_raw = check("预处理后的连续数据（_raw.fif）")
        self.c_epo = check("分段数据（-epo.fif，第 3、4 章可直接读取）", True)
        self.c_ave = check("各条件 ERP（-ave.fif）")
        self.c_tfr = check("时频结果（-tfr.h5）")
        self.c_mat = check("分段数组 .mat（每个条件一个文件，通道×时间×试次，µV，手册第 3 章格式）")
        self.c_npy = check("分段数组 .npy（试次×通道×时间，V）与时频数组 .npy")
        self.c_log = check("处理记录（.txt）", True)
        self.add(group("导出位置", rows=[("文件夹", self.folder),
                                       (None, _browse_line(self, self.folder, "选择文件夹", "", folder=True)),
                                       ("文件名前缀", self.name)]))
        self.add(group("导出内容", widgets=[self.c_raw, self.c_epo, self.c_ave, self.c_tfr, self.c_mat,
                                         self.c_npy, self.c_log,
                                         [button("导出", self.export, primary=True), "stretch"]]))
        self.out = info_box(100)
        self.add(group("结果", widgets=[self.out]))
        self.finish()

    def export(self):
        st = self.state
        if st.raw is None:
            QMessageBox.information(self, "没有数据", "请先读取数据。")
            return
        folder = self.folder.text().strip()
        os.makedirs(folder, exist_ok=True)
        base = self.name.text().strip()
        if not base:
            stem = os.path.basename(str(st.raw_path or "eeg"))
            base = stem.split(".")[0]
        opts = dict(raw=self.c_raw.isChecked(), epo=self.c_epo.isChecked(), ave=self.c_ave.isChecked(),
                    tfr=self.c_tfr.isChecked(), mat=self.c_mat.isChecked(), npy=self.c_npy.isChecked(),
                    log=self.c_log.isChecked())

        def work():
            import mne
            import scipy.io as sio
            out = []
            j = lambda s: os.path.join(folder, base + s)  # noqa: E731
            if opts["raw"]:
                st.raw.save(j("_raw.fif"), overwrite=True)
                out.append(j("_raw.fif"))
            ep = st.epochs
            if ep is not None:
                if opts["epo"]:
                    ep.save(j("-epo.fif"), overwrite=True)
                    out.append(j("-epo.fif"))
                if opts["ave"]:
                    evs = [ep[c].average() for c in ep.event_id]
                    for e, c in zip(evs, ep.event_id):
                        e.comment = c
                    mne.write_evokeds(j("-ave.fif"), evs, overwrite=True)
                    out.append(j("-ave.fif"))
                if opts["mat"]:
                    for c in ep.event_id:
                        arr = ep[c].get_data(picks="eeg") * 1e6
                        p = j(f"_{c}.mat")
                        sio.savemat(p, {"data": np.transpose(arr, (1, 2, 0)),
                                        "sfreq": ep.info["sfreq"], "tmin": ep.tmin,
                                        "ch_names": np.array(ep.copy().pick("eeg").ch_names, dtype=object)})
                        out.append(p)
                if opts["npy"]:
                    arr = ep.get_data()
                    np.save(j("_epochs.npy"), arr)
                    out.append(f"{j('_epochs.npy')}　形状 {arr.shape}")
            elif opts["epo"] or opts["ave"] or opts["mat"]:
                log("还没有分段，跳过分段相关导出")
            if st.power is not None:
                if opts["tfr"]:
                    st.power.save(j("-tfr.h5"), overwrite=True)
                    out.append(j("-tfr.h5"))
                if opts["npy"]:
                    np.save(j("_power.npy"), st.power.data)
                    out.append(f"{j('_power.npy')}　形状 {st.power.data.shape}")
            if opts["log"]:
                with open(j("_处理记录.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(st.history) + "\n")
                out.append(j("_处理记录.txt"))
            return out

        self.run("导出结果", work, lambda out: self.out.setHtml("已导出：<br>" + "<br>".join(out)))
