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
        r = pj.add_raw_files(p, [compat.resource_path("sample_data", "eeglab_data.set")])[0]
        s = p.get_rec(r.id)[0]
        s.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
        s.rename_from_locs = True
        p.pipeline = pj.Pipeline(notch=60, tmin=-1, tmax=2, bl0=-0.5, bl1=0, reject_uv=200, ica_n="15")
        ep = pj.run_pipeline(p, s, log=quiet)
        p.save()
        return f"{len(ep)} epochs, ICA 去除 {r.ica_exclude}"
    check("原始数据预处理（读取、电极、滤波、ICA、分段）", raw_pipeline)

    def event_tools():
        from .core import events as ev
        import pandas as pd
        tab = pd.DataFrame({"latency": [1.0, 2.5, 4.0], "marker_value": [901, 902, 1]})
        df = ev.table_to_events(tab, ev.guess_columns(tab), 128, rec_duration=10)
        df, _ = ev.apply_rules(df, ev.parse_rules("9xx=刺激"))
        df = ev.merge(df, ev.fixed_interval(0, 6, 2, "片段"))
        return " ".join(f"{n}×{c}" for n, c, _ in ev.summary(df))
    check("事件工具（表格导入、改名、固定间隔）", event_tools)

    # ---- 界面里每个可选项都实际跑一遍，防止“选项能选、运行时缺组件”
    def optional_packages():
        import importlib
        need = ["picard", "sklearn", "mffpy", "pymatreader", "curryreader", "antio", "pymef", "h5py", "edfio",
                "eeglabio", "pybv", "openpyxl", "xlrd", "pingouin", "neurora", "inverted_encoding", "mne_qt_browser"]
        missing = []
        for m in need:
            try:
                importlib.import_module(m)
            except Exception as e:  # noqa: BLE001
                missing.append(f"{m}（{type(e).__name__}）")
        if missing:
            raise RuntimeError("缺少：" + "，".join(missing))
        return f"{len(need)} 个"
    check("可选组件都已打包", optional_packages)

    def ica_methods():
        import numpy as np
        from .core import single
        rng = np.random.default_rng(0)
        info = mne.create_info([f"EEG{i}" for i in range(8)], 100.0, "eeg")
        raw = mne.io.RawArray(rng.laplace(size=(8, 3000)) * 1e-5, info, verbose=False)
        out = []
        for m in ("fastica", "infomax", "picard"):
            ica, _ = single.fit_ica(raw, 5, m, 1.0, 97, log=quiet)
            out.append(f"{m} {ica.n_components_}")
        return ", ".join(out)
    check("ICA 三种算法", ica_methods)

    def file_formats():
        import numpy as np
        import pandas as pd
        from .core import events as ev, reading
        rng = np.random.default_rng(1)
        info = mne.create_info(["Fz", "Cz", "Pz"], 128.0, "eeg")
        raw = mne.io.RawArray(rng.normal(size=(3, 1280)) * 1e-5, info, verbose=False)
        raw.set_annotations(mne.Annotations([1.0, 3.0], [0, 0], ["a", "b"]))
        d = os.path.join(tmp, "fmt")
        os.makedirs(d, exist_ok=True)
        ok = []
        for ext in ("edf", "vhdr", "set", "fif"):
            f = os.path.join(d, f"t_raw.{ext}" if ext == "fif" else f"t.{ext}")
            if ext == "fif":
                raw.save(f, overwrite=True, verbose=False)
            else:
                mne.export.export_raw(f, raw, overwrite=True, verbose=False)
            r, _ = reading.read_raw_any(f, log=quiet)
            ok.append(f"{ext}:{len(ev.from_raw(r))}")
        tab = pd.DataFrame({"time": [1.0, 2.0], "code": [1, 2]})
        for ext in ("xlsx", "csv", "txt"):
            f = os.path.join(d, f"ev.{ext}")
            if ext == "xlsx":
                tab.to_excel(f, index=False)
            else:
                tab.to_csv(f, index=False, sep="," if ext == "csv" else "\t")
            df = ev.table_to_events(ev.read_table(f), ev.guess_columns(ev.read_table(f)), 128, rec_duration=10)
            ok.append(f"{ext}:{len(df)}")
        return " ".join(ok)
    check("文件格式读写（EDF/BrainVision/EEGLAB/FIF，事件表 xlsx/csv/txt）", file_formats)

    def all_tests():
        import numpy as np
        from .core import stats
        rng = np.random.default_rng(2)
        mk = lambda k: [rng.normal(i * 0.3, 1, (12, 20)) for i in range(k)]  # noqa: E731
        n = 0
        for level in ("group", "trial"):
            for k, conds in ((1, mk(1)), (2, mk(2)), (4, mk(4))):
                for t in stats.tests_for(k, level):
                    for c in stats.CORRECTIONS:
                        fl = (2, 2) if "两因素" in t else None
                        stats.run_time_test(conds, t, c, n_iter=50, factor_levels=fl, log=quiet)
                        n += 1
        return f"{n} 种组合"
    check("所有统计检验 × 多重比较校正", all_tests)
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

    def rsa_options():
        from .core import advanced
        out = []
        dd = analysis.decoding_data(st["p"], analysis.GROUP, "朝向", [str(i * 45) for i in range(8)], log=quiet)
        for kind in ("环形距离（如朝向、位置）", "线性距离", "类别是否相同（0/1）"):
            for dist in advanced.RDM_METHODS:
                r = analysis.rsa(dd, kind, "", 20, 20, distance=dist, n_iter=20, log=quiet)
                out.append(r.metrics[0][1])
        return f"{len(out)} 种组合"
    check("RSA 模型 × 距离", rsa_options)

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
