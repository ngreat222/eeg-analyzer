"""打包后的自检：脑电分析软件.exe --selftest 输出文件

在一个临时项目里走完整流程：原始数据预处理（含 ICA）、合成多被试项目、ERP / 时频 / 解码 / IEM / RSA、
报告导出、界面构建，确认打包时没有漏掉模块或数据文件。
"""
import os
import shutil
import tempfile
import time
import traceback


def run(out_path):
    lines = []

    def check(name, fn):
        t0 = time.time()
        try:
            info = fn()
            lines.append(f"OK   {name} ({time.time() - t0:.1f}s) {info or ''}")
        except Exception:  # noqa: BLE001
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    from . import compat
    compat.setup()
    import mne
    mne.set_log_level("ERROR")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from .core import analysis, project as pj
    tmp = tempfile.mkdtemp(prefix="eeg_selftest_")
    quiet = lambda m: None  # noqa: E731
    st = {}

    def raw_pipeline():
        p = pj.Project(os.path.join(tmp, "raw"))
        s = pj.add_raw_files(p, [compat.resource_path("sample_data", "eeglab_data.set")])[0]
        s.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
        s.rename_from_locs = True
        p.pipeline = pj.Pipeline(notch=60, tmin=-1, tmax=2, bl0=-0.5, bl1=0, reject_uv=200, ica_n="15")
        ep = pj.run_pipeline(p, s, log=quiet)
        p.save()
        return f"{len(ep)} epochs, ICA 去除 {s.ica_exclude}"
    check("原始数据预处理（读取、电极、滤波、ICA、分段）", raw_pipeline)
    check("标准电极模板", lambda: len(mne.channels.make_standard_montage("standard_1020").ch_names))

    def demo():
        st["p"] = pj.make_demo_project(os.path.join(tmp, "demo"), n_subs=4, n_per_cond=48, log=quiet)
        return st["p"].summary()["n"]
    check("合成演示项目", demo)
    C = ["First", "Repetition"]
    check("ERP 全组 + cluster", lambda: analysis.erp(st["p"], analysis.GROUP, C, "Pz", (-0.2, 0.8), (0.3, 0.5),
                                                  "cluster 置换检验（配对，双侧）", "不校正", n_iter=100,
                                                  log=quiet).metrics[0][1])
    check("ERP 单被试 + FDR", lambda: len(analysis.erp(st["p"], "sub01", C, "Pz", (-0.2, 0.8), (0.3, 0.5),
                                                     "独立样本 t 检验", "FDR", log=quiet).figures))
    check("时频 + cluster", lambda: analysis.tfr(st["p"], analysis.GROUP, C, "Oz", 4, 30, 4, 2, (-0.4, -0.1),
                                               (-0.2, 0.8), (8, 12), (0.2, 0.6), n_iter=50, decim=2,
                                               log=quiet).metrics[0][1])

    def dec():
        dd = analysis.decoding_data(st["p"], analysis.GROUP, "朝向", [str(i * 45) for i in range(8)], log=quiet)
        out = []
        for m in ("逐时间点解码", "跨时间泛化", "反向编码模型 IEM"):
            r = analysis.decode(dd, m, 3, 10, 10, 3, 1, n_iter=50, num_perm=100, log=quiet)
            out.append(r.metrics[0][1])
        r = analysis.rsa(dd, "环形距离（如朝向、位置）", "", 10, 10, n_iter=50, log=quiet)
        out.append(r.metrics[0][1])
        st["res"] = r
        return " / ".join(out)
    check("解码、跨时间泛化、IEM、RSA", dec)

    def ui():
        from .ui import main_window
        from .ui.report_page import export_report
        w = main_window.MainWindow()
        w.project = st["p"]  # 不经过 set_project，避免改写用户的“上次打开的项目”
        w.refresh()
        for name, _, _ in main_window.NAV:
            w.goto(name)
        w.pages["ERP"].view.show_result(st["res"])
        path = export_report(os.path.join(tmp, "report"), "自检", [(__import__("datetime").datetime.now(),
                                                                     st["res"])])
        b = st["p"].epochs("sub01").plot(show=False, block=False)
        name = type(b).__name__
        b.close()
        w.close()
        return f"{len(main_window.NAV)} 页, 报告 {os.path.getsize(path) // 1024} KB, 浏览器 {name}"
    check("界面、报告导出、Qt 波形浏览器", ui)

    ok = all(line.startswith("OK") for line in lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + f"\n\n{'全部通过' if ok else '有失败项'}\n")
    shutil.rmtree(tmp, ignore_errors=True)
    app.quit()
    return 0 if ok else 1
