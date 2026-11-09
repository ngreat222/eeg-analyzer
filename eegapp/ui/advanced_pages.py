"""第 4 章高级分析页面：逐时间点解码、跨时间解码、RSA、IEM。"""
import os

import numpy as np
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core import advanced, arrays
from .common import Page, button, check, combo, fspin, info_box, ispin, line, log, note
from .common import group as box
from .files_widget import FileList


def _need_dec(page):
    if page.state.dec is None:
        QMessageBox.information(page, "没有数据", "请先在第 14 步读取解码数据。")
        return False
    return True


def _save_npz(page, default, **arrs):
    p, _ = QFileDialog.getSaveFileName(page, "保存结果", os.path.join(os.path.expanduser("~/Desktop"), default),
                                       "NumPy (*.npz)")
    if p:
        np.savez(p, **arrs)
        log(f"已保存 {p}")


class StatsBox:
    """第 4 章各页共用的统计与显示参数。"""

    def __init__(self, page, stats_t=(0.0, 1.5), cluster_default=True, ylim=None):
        self.cluster = check("cluster 置换检验（否则逐点单样本 t 检验）", cluster_default)
        self.p = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.cp = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.niter = ispin(1000, 10, 100000, 100)
        self.s0 = fspin(stats_t[0], -60, 60, 0.1, 3, " s")
        self.s1 = fspin(stats_t[1], -60, 60, 0.1, 3, " s")
        self.ylim_on = check("固定 Y 轴范围", ylim is not None)
        ylim = ylim or (0, 1)
        self.y0 = fspin(ylim[0], -1e6, 1e6, 0.01, 4)
        self.y1 = fspin(ylim[1], -1e6, 1e6, 0.01, 4)
        page.add(box("统计与显示", rows=[(None, self.cluster), ("p 阈值", self.p), ("cluster 水平 p", self.cp),
                                      ("置换次数", self.niter), ("检验时间从", self.s0), ("到", self.s1),
                                      (None, self.ylim_on), ("Y 下限", self.y0), ("Y 上限", self.y1)]))

    def args(self):
        return dict(use_cluster=self.cluster.isChecked(), p=self.p.value(), clusterp=self.cp.value(),
                    n_iter=self.niter.value(), stats_time=(self.s0.value(), self.s1.value()),
                    ylim=(self.y0.value(), self.y1.value()) if self.ylim_on.isChecked() else None)


class DecodeParams:
    def __init__(self, page):
        self.navg = ispin(13, 1, 1000)
        self.win = ispin(5, 1, 1000)
        self.step = ispin(5, 1, 1000)
        self.folds = ispin(3, 2, 50)
        self.reps = ispin(10, 1, 1000)
        self.smooth = check("结果平滑", True)
        self.norm = check("特征标准化")
        self.pca = check("PCA 降维（保留 95% 方差）")
        self.jobs = ispin(1, 1, max(1, os.cpu_count() or 1))
        page.add(box("解码参数", rows=[("每次平均的试次数 navg", self.navg), ("时间窗（采样点）", self.win),
                                    ("步长（采样点）", self.step), ("交叉验证折数", self.folds),
                                    ("重复次数", self.reps), (None, self.smooth), (None, self.norm),
                                    (None, self.pca), ("并行线程数", self.jobs)],
                     widgets=[note("手册：16 类朝向，navg=13，时间窗 5、步长 5（250 Hz 下即 20 ms），"
                                   "3 折交叉验证重复 10 次。分类器为线性 SVM。")]))

    def args(self):
        return dict(navg=self.navg.value(), time_win=self.win.value(), time_step=self.step.value(),
                    nfolds=self.folds.value(), nrepeats=self.reps.value(), normalization=self.norm.isChecked(),
                    pca=self.pca.isChecked(), n_jobs=self.jobs.value(), do_smooth=self.smooth.isChecked())


# ====================================================================== 数据
class DecDataPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "14. 解码数据与标签",
                         "对应手册第 4 章代码 2–4。每个被试一个数据文件，再配一个标签文件（每个试次一个类别）。"
                         "-epo.fif 分段文件可以不配标签文件，直接用事件类型当标签。")
        self.files = FileList(arrays.GROUP_FILE_FILTER, "选择每个被试的数据文件", on_change=self._keys)
        self.key = combo([])
        self.key.setEditable(True)
        self.dim = combo(list(arrays.DIM_ORDERS), list(arrays.DIM_ORDERS)[1])
        self.sfreq = fspin(250, 1, 100000, 50, 2, " Hz")
        self.tmin = fspin(-1.5, -60, 60, 0.1, 4, " s")
        self.add(box("数据文件（按被试顺序）", widgets=[self.files]))
        self.add(box("数组文件的格式", rows=[("变量名", self.key), ("维度顺序", self.dim), ("采样率", self.sfreq),
                                        ("分段起点时间", self.tmin)],
                     widgets=[note("手册：ERP201.mat 等文件里的 filtData 为 试次×通道×时间（640×27×750），"
                                   "250 Hz，从 −1.5 s 开始。")]))
        self.labels = FileList(arrays.LABEL_FILE_FILTER, "选择每个被试的标签文件")
        self.col = ispin(2, 1, 100)
        self.lname = line("朝向", "这组标签叫什么，用于图标题")
        self.add(box("标签文件（与数据文件一一对应；-epo.fif 可留空）", rows=[("用第几列", self.col),
                                                                     ("标签名称", self.lname)],
                     widgets=[self.labels, note("手册：labels/ori_201.txt 第 2 列是 0–15 的朝向类别；"
                                                "pos_201.txt 是位置类别。")]))
        self.crop_on = check("只用一段时间", True)
        self.c0 = fspin(-0.5, -60, 60, 0.1, 3, " s")
        self.c1 = fspin(1.5, -60, 60, 0.1, 3, " s")
        self.add(box("时间范围", rows=[(None, self.crop_on), ("从", self.c0), ("到", self.c1)],
                     widgets=[[button("读取", self.load, primary=True), "stretch"]]))
        self.info = info_box(90)
        self.add(box("数据概况", widgets=[self.info]))
        self.finish()

    def _keys(self):
        fs = self.files.files()
        self.key.clear()
        if fs and not arrays.is_mne_file(fs[0]):
            try:
                self.key.addItems(arrays.list_keys(fs[0]))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "读取文件", str(e))

    def load(self):
        files = self.files.files()
        if not files:
            QMessageBox.information(self, "数据文件", "请添加每个被试的数据文件。")
            return
        labels = self.labels.files()
        if not labels and not all(arrays.is_mne_file(f) for f in files):
            QMessageBox.information(self, "标签文件", "数组文件需要配标签文件。")
            return
        key = self.key.currentText().strip()
        key = "" if key == "(整个数组)" else key
        dim = arrays.DIM_ORDERS[self.dim.currentText()]
        crop = (self.c0.value(), self.c1.value()) if self.crop_on.isChecked() else None
        args = (files, key, dim, labels, self.col.value() - 1, self.sfreq.value(), self.tmin.value(), crop,
                self.lname.text().strip() or "标签")

        def done(dd):
            self.state.dec = dd
            self.state.dec_result = {}
            self.info.setHtml(dd.summary())
            self.ctx.data_changed()

        self.run("读取解码数据", lambda: advanced.load_decoding(*args, log=log), done)

    def on_show(self):
        if self.state.dec is not None:
            self.info.setHtml(self.state.dec.summary())


# ====================================================================== 逐时间点解码
class TbytPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "15. 逐时间点解码",
                         "对应手册第 4 章代码 5–8（NeuroRA tbyt_decoding_kfold）。"
                         "在每个时间窗上训练分类器，看什么时候能从脑电里解码出刺激类别。")
        self.params = DecodeParams(self)
        self.stats = StatsBox(self)
        self.add(box("", widgets=[[button("开始解码", self.compute, primary=True),
                                   button("保存结果 .npz", self.save), "stretch"]]))
        self.finish()

    def compute(self):
        if not _need_dec(self):
            return
        dd, pa, sa = self.state.dec, self.params.args(), self.stats.args()

        def work():
            acc = advanced.tbyt_decoding(dd, **pa)
            t = advanced.window_times(dd, acc.shape[1], pa["time_win"], pa["time_step"])
            chance = 1 / dd.n_classes
            mask = advanced.stats_mask_1d(acc, chance, t, sa["stats_time"], sa["p"], sa["clusterp"],
                                          sa["n_iter"], sa["use_cluster"])
            log(f"显著时间窗：{int(mask.sum())} 个；峰值正确率 {acc.mean(0).max():.3f}（机会水平 {chance:.4f}）")
            fig = advanced.plot_timecourse(acc, t, mask, chance, sa["ylim"],
                                           title=f"{dd.label_name}：逐时间点解码")
            return acc, t, mask, fig

        def done(r):
            acc, t, mask, fig = r
            self.state.dec_result["tbyt"] = dict(accuracy=acc, times=t, significant=mask)
            self.show_fig(fig, f"解码 {dd.label_name}")

        self.run("逐时间点解码", work, done)

    def save(self):
        r = self.state.dec_result.get("tbyt")
        if r:
            _save_npz(self, "逐时间点解码.npz", **r)


# ====================================================================== 跨时间解码
class CTPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "16. 跨时间解码",
                         "对应手册第 4 章代码 9–12（NeuroRA ct_decoding_kfold）。在一个时间点训练、"
                         "在所有时间点测试，看神经表征是否随时间保持稳定。计算量较大。")
        self.params = DecodeParams(self)
        self.stats = StatsBox(self)
        self.clim_on = check("固定色标范围")
        self.c0 = fspin(0.06, -1, 1, 0.005, 4)
        self.c1 = fspin(0.075, -1, 1, 0.005, 4)
        self.add(box("色标", rows=[(None, self.clim_on), ("下限", self.c0), ("上限", self.c1)],
                     widgets=[[button("开始解码", self.compute, primary=True),
                               button("保存结果 .npz", self.save), "stretch"]]))
        self.finish()

    def compute(self):
        if not _need_dec(self):
            return
        dd, pa, sa = self.state.dec, self.params.args(), self.stats.args()
        clim = (self.c0.value(), self.c1.value()) if self.clim_on.isChecked() else None

        def work():
            acc = advanced.ct_decoding(dd, **pa)
            t = advanced.window_times(dd, acc.shape[1], pa["time_win"], pa["time_step"])
            chance = 1 / dd.n_classes
            mask = None
            if sa["use_cluster"]:
                mask = advanced.stats_mask_2d(acc, chance, t, sa["stats_time"], sa["p"], sa["clusterp"],
                                              sa["n_iter"])
            fig = advanced.plot_ct(acc.mean(0), t, mask, clim, f"{dd.label_name}：跨时间解码")
            return acc, t, mask, fig

        def done(r):
            acc, t, mask, fig = r
            self.state.dec_result["ct"] = dict(accuracy=acc, times=t,
                                               significant=mask if mask is not None else np.zeros(0))
            self.show_fig(fig, f"跨时间解码 {dd.label_name}")

        self.run("跨时间解码", work, done)

    def save(self):
        r = self.state.dec_result.get("ct")
        if r:
            _save_npz(self, "跨时间解码.npz", **r)


# ====================================================================== RSA
class RSAPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "17. 表征相似性分析 (RSA)",
                         "对应手册第 4 章代码 13–17（NeuroRA eegRDM、rdms_corr）。按类别计算脑电的表征差异矩阵（RDM），"
                         "再逐时间窗与假设的模型 RDM 做 Spearman 相关。")
        self.kind = combo(["环形距离（如朝向、位置）", "线性距离", "类别是否相同（0/1）", "从文件读取"])
        self.mfile = line("", "n×n 矩阵，.npy / .txt / .csv")
        self.win = ispin(5, 1, 1000)
        self.step = ispin(5, 1, 1000)
        self.method = combo(["correlation", "euclidean", "mahalanobis"])
        self.add(box("模型 RDM 与参数", rows=[("模型", self.kind), ("模型文件", self.mfile),
                                         ("时间窗（采样点）", self.win), ("步长（采样点）", self.step),
                                         ("RDM 距离", self.method)],
                     widgets=[[button("选择模型文件…", self._pick), button("画模型 RDM", self.show_model), "stretch"],
                              note("手册的朝向模型：16 个朝向首尾相连，相邻差 1/8，相对（差 180°）最大为 1。")]))
        self.stats = StatsBox(self, ylim=None)
        self.add(box("", widgets=[[button("计算 RSA", self.compute, primary=True),
                                   button("保存结果 .npz", self.save), "stretch"]]))
        self.finish()

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "模型 RDM", os.path.expanduser("~/Desktop"),
                                           "矩阵 (*.npy *.txt *.csv);;所有文件 (*)")
        if p:
            self.mfile.setText(p)
            self.kind.setCurrentText("从文件读取")

    def _model(self):
        return advanced.model_rdm(self.kind.currentText(), self.state.dec.n_classes, self.mfile.text().strip())

    def show_model(self):
        if not _need_dec(self):
            return
        try:
            m = self._model()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "模型 RDM", str(e))
            return
        self.show_fig(advanced.plot_rdm(m, self.state.dec.class_names, "模型 RDM"), "模型 RDM")

    def compute(self):
        if not _need_dec(self):
            return
        try:
            model = self._model()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "模型 RDM", str(e))
            return
        dd, sa = self.state.dec, self.stats.args()
        win, step, method = self.win.value(), self.step.value(), self.method.currentText()

        def work():
            rdms, r, n_min = advanced.rsa(dd, model, win, step, method)
            log(f"每个类别每个被试使用 {n_min} 个试次")
            t = advanced.window_times(dd, r.shape[1], win, step)
            mask = advanced.stats_mask_1d(r, 0, t, sa["stats_time"], sa["p"], sa["clusterp"], sa["n_iter"],
                                          sa["use_cluster"])
            figs = [(advanced.plot_timecourse(r, t, mask, None, sa["ylim"], "与模型 RDM 的相关 (r)",
                                              f"{dd.label_name}：RSA", color="#1f77b4"), "RSA"),
                    (advanced.plot_rdm(rdms.mean(axis=(0, 1)), dd.class_names, "脑电 RDM（被试与时间平均）"),
                     "脑电 RDM")]
            return rdms, r, t, mask, figs

        def done(res):
            rdms, r, t, mask, figs = res
            self.state.dec_result["rsa"] = dict(rdms=rdms, similarity=r, times=t, significant=mask, model=model)
            for f, ti in figs:
                self.show_fig(f, ti)

        self.run("RSA", work, done)

    def save(self):
        r = self.state.dec_result.get("rsa")
        if r:
            _save_npz(self, "RSA结果.npz", **r)


# ====================================================================== IEM
class IEMPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "18. 反向编码模型 (IEM)",
                         "对应手册第 4 章代码 18–22（inverted_encoding）。从脑电重建刺激特征（如朝向），"
                         "用预测误差 MAE 衡量；ΔMAE = 置换分布均值 − 实际 MAE，越大说明重建越好。")
        self.win = ispin(5, 1, 1000)
        self.folds = ispin(5, 2, 50)
        self.circ = check("特征是环形的（朝向、角度、位置）", True)
        self.nperm = ispin(5000, 100, 1000000, 1000)
        self.p = fspin(0.05, 0.0001, 0.5, 0.01, 4)
        self.ylim_on = check("固定 Y 轴范围")
        self.y0 = fspin(-0.1, -100, 100, 0.1, 3)
        self.y1 = fspin(0.6, -100, 100, 0.1, 3)
        self.add(box("参数", rows=[("时间窗（采样点平均）", self.win), ("交叉验证折数", self.folds), (None, self.circ),
                                 ("置换次数", self.nperm), ("p 阈值", self.p), (None, self.ylim_on),
                                 ("Y 下限", self.y0), ("Y 上限", self.y1)],
                     widgets=[note("检验方法与手册相同：每个时间窗把各被试的 MAE 与置换分布做单侧独立样本 t 检验。"),
                              [button("计算 IEM", self.compute, primary=True), button("保存结果 .npz", self.save),
                               "stretch"]]))
        self.finish()

    def compute(self):
        if not _need_dec(self):
            return
        dd = self.state.dec
        win, folds, circ, nperm, p = (self.win.value(), self.folds.value(), self.circ.isChecked(),
                                      self.nperm.value(), self.p.value())
        ylim = (self.y0.value(), self.y1.value()) if self.ylim_on.isChecked() else None

        def work():
            t, mae, null = advanced.iem(dd, win, folds, circ, nperm, progress=log)
            mask, ps = advanced.iem_stats(mae, null, p)
            log(f"显著时间窗：{int(mask.sum())} 个")
            fig = advanced.plot_iem(t, mae, null, mask, ylim, f"{dd.label_name}：IEM 重建")
            return t, mae, null, mask, ps, fig

        def done(r):
            t, mae, null, mask, ps, fig = r
            self.state.dec_result["iem"] = dict(times=t, mae=mae, null_mae=null, significant=mask, p=ps)
            self.show_fig(fig, f"IEM {dd.label_name}")

        self.run("IEM", work, done)

    def save(self):
        r = self.state.dec_result.get("iem")
        if r:
            _save_npz(self, "IEM结果.npz", **r)
