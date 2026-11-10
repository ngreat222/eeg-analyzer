"""ERP、时频、解码、RSA 四个分析页。每页都可以选全组或单个被试，统计检验在设置里一起选。"""
import os

from PySide6.QtWidgets import QFileDialog

from ..core import advanced, analysis, stats
from ..core.analysis import GROUP
from ..core.single import parse_floats
from .base import BasePage, label_values, refresh_scope, scope_combo
from .common import combo, fspin, ispin, line, log
from .widgets import CheckList, EmptyState, ResultView, btn, checkbox, hbox, label


def _pair(a, b):
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.setLayout(hbox(a, label("到", "Muted"), b, spacing=6))
    return w


class _AnalysisPage(BasePage):
    icon_name = "mdi6.chart-line"
    empty_title = ""
    empty_body = ""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.view = ResultView(EmptyState(self.icon_name, self.empty_title, self.empty_body))
        self.body.addWidget(self.view, 1)

    def show_res(self, res):
        self.view.show_result(res)
        self.ctx.add_report(res)


# ====================================================================== ERP
class ERPPage(_AnalysisPage):
    title = "ERP 事件相关电位"
    subtitle = "叠加平均、条件比较、逐时间点统计、地形图，一次生成"
    icon_name = "mdi6.sine-wave"
    empty_title = "看看大脑对刺激的反应"
    empty_body = "在右侧选好条件和通道，点「运行 ERP 分析」。选「全组」时以被试为单位做统计，选单个被试时以试次为单位。"

    def __init__(self, ctx):
        super().__init__(ctx)
        s = self.section("数据")
        self.scope = s.row("范围", scope_combo())
        self.conds = s.row("条件", CheckList(120))
        s = self.section("通道与时间")
        self.ch = s.row("通道", line("Pz, Cz", "名字或序号，多个取平均"))
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
        self.scope.currentIndexChanged.connect(self._tests)
        self.conds.changed.connect(self._tests)

    def on_show(self):
        super().on_show()
        p = self.project
        refresh_scope(self.scope, p)
        if p and p.done_subjects():
            self.conds.set_items(p.conditions())
        self._tests()

    def _tests(self):
        k = len(self.conds.checked())
        level = "group" if self.scope.currentData() == GROUP else "trial"
        cur = self.test.currentText()
        self.test.clear()
        self.test.addItem("不做检验")
        items = stats.tests_for(k, level)
        self.test.addItems(items)
        if cur in items:  # 只保留仍然适用的检验；之前没数据时的“不做检验”不算用户选择
            self.test.setCurrentText(cur)
        elif items:
            pref = [x for x in items if x.startswith("cluster")]
            self.test.setCurrentText(pref[0] if pref else items[0])

    def go(self):
        if not self.need_project():
            return
        args = dict(scope=self.scope.currentData(), conditions=self.conds.checked(), channels=self.ch.text(),
                    win=(self.w0.value(), self.w1.value()), metric_win=(self.m0.value(), self.m1.value()),
                    test=self.test.currentText(), correction=self.corr.currentText(), alpha=self.alpha.value(),
                    n_iter=self.niter.value(), cluster_p=self.alpha.value(),
                    factor_levels=[int(x) for x in parse_floats(self.levels.text())] or None,
                    topo_times=self.topo.text().strip())
        p = self.project
        self.run("ERP 分析", lambda: analysis.erp(p, log=log, **args), self.show_res)


# ====================================================================== 时频
class TFRPage(_AnalysisPage):
    title = "时频与频谱"
    subtitle = "Morlet 小波时频图、cluster 置换检验、频段功率地形图"
    icon_name = "mdi6.chart-bell-curve-cumulative"
    empty_title = "节律的起伏，一目了然"
    empty_body = "选 1 个或 2 个条件和感兴趣的通道（例如枕区 O1, Oz, O2 看 alpha），点「运行时频分析」。"

    def __init__(self, ctx):
        super().__init__(ctx)
        s = self.section("数据")
        self.scope = s.row("范围", scope_combo())
        self.conds = s.row("条件（1–2 个）", CheckList(110))
        self.ch = s.row("通道", line("O1, Oz, O2", "多个取平均"))
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
        self.p = s.row("p 阈值", fspin(0.05, 0.0001, 0.5, 0.01, 3))
        self.niter = s.row("置换次数", ispin(500, 50, 100000, 100))
        self.end_side()
        self.action_row(btn("运行时频分析", self.go, "primary", "mdi6.play"))

    def on_show(self):
        super().on_show()
        p = self.project
        refresh_scope(self.scope, p)
        if p and p.done_subjects():
            conds = p.conditions()
            self.conds.set_items(conds, checked=self.conds.checked() or conds[:2])

    def go(self):
        if not self.need_project():
            return
        p = self.project
        a = dict(scope=self.scope.currentData(), conditions=self.conds.checked(), channels=self.ch.text(),
                 fmin=self.f0.value(), fmax=self.f1.value(), fstep=self.fs.value(), cycles_div=self.div.value(),
                 baseline=(self.b0.value(), self.b1.value()), win=(self.w0.value(), self.w1.value()),
                 metric_band=(self.mb0.value(), self.mb1.value()), metric_win=(self.mt0.value(), self.mt1.value()),
                 do_stats=self.do_stat.isChecked(), p=self.p.value(), cluster_p=self.p.value(),
                 n_iter=self.niter.value(), decim=self.decim.value())
        self.run("时频分析", lambda: analysis.tfr(p, log=log, **a), self.show_res)


# ====================================================================== 解码 / RSA 共用的数据选择
class _LabelPage(_AnalysisPage):
    def _data_section(self):
        s = self.section("数据与标签")
        self.scope = s.row("范围", scope_combo())
        self.label = s.row("标签", combo([]))
        self.classes = s.row("类别", CheckList(140))
        self.label.currentTextChanged.connect(self._classes)

    def _stats_section(self, stats_t=(0.0, 1.0)):
        s = self.section("统计检验")
        self.cluster = s.widget(checkbox("cluster 置换检验（否则逐点 t 检验）", True))
        self.p = s.row("p 阈值", fspin(0.05, 0.0001, 0.5, 0.01, 3))
        self.niter = s.row("置换次数", ispin(1000, 50, 100000, 100))
        self.s0, self.s1 = fspin(stats_t[0], -60, 60, 0.1, 3, " s"), fspin(stats_t[1], -60, 60, 0.1, 3, " s")
        s.row("检验时间段", _pair(self.s0, self.s1))

    def on_show(self):
        super().on_show()
        p = self.project
        refresh_scope(self.scope, p)
        if p and p.done_subjects():
            cur = self.label.currentText()
            self.label.blockSignals(True)
            self.label.clear()
            cols = p.label_columns()
            self.label.addItems(cols)
            if cur in cols:
                self.label.setCurrentText(cur)
            elif len(cols) > 1:
                self.label.setCurrentIndex(1)
            self.label.blockSignals(False)
            self._classes()

    def _classes(self):
        p = self.project
        if p and p.done_subjects() and self.label.currentText():
            self.classes.set_items(label_values(p, self.label.currentText()), default_all=True)

    def _dd(self):
        p = self.project
        return lambda: analysis.decoding_data(p, self.scope.currentData(), self.label.currentText(),
                                              self.classes.checked(), log=log)


class DecodePage(_LabelPage):
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
        mk = self._dd()
        a = dict(method=self.method.currentText(), navg=self.navg.value(), time_win=self.win.value(),
                 time_step=self.step.value(), nfolds=self.folds.value(), nrepeats=self.reps.value(),
                 use_cluster=self.cluster.isChecked(), p=self.p.value(), cluster_p=self.p.value(),
                 n_iter=self.niter.value(), stats_time=(self.s0.value(), self.s1.value()),
                 circular=self.circ.isChecked(), num_perm=self.nperm.value(), n_jobs=self.jobs.value())
        self.run(a["method"], lambda: analysis.decode(mk(), log=log, **a), self.show_res)


class RSAPage(_LabelPage):
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
        mk = self._dd()
        a = dict(model_kind=self.kind.currentText(), model_file=self.mfile.text().strip(), time_win=self.win.value(),
                 time_step=self.step.value(), distance=self.dist.currentText(), use_cluster=self.cluster.isChecked(),
                 p=self.p.value(), cluster_p=self.p.value(), n_iter=self.niter.value(),
                 stats_time=(self.s0.value(), self.s1.value()))
        self.run("RSA", lambda: analysis.rsa(mk(), log=log, **a), self.show_res)
