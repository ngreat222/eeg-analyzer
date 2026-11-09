"""打包后的自检：脑电分析软件.exe --selftest 输出文件

逐项调用各章用到的库（读数据、滤波、ICA、分段、时频、出图、NeuroRA 解码/RSA/cluster、IEM、
Qt 波形浏览器），把每项的结果写到输出文件，用来确认打包时没有漏掉模块或数据文件。
"""
import os
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
    import numpy as np
    mne.set_log_level("ERROR")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from .core import advanced, demo, group, reading, single, stats
    st = {}

    def read():
        raw, _ = reading.read_raw_any(compat.resource_path("sample_data", "eeglab_data.set"))
        single.apply_channel_setup(raw, "file", compat.resource_path("sample_data", "eeglab_chan32.locs"),
                                   True, None, log=lambda m: None)
        st["raw"] = raw
        return f"{len(raw.ch_names)} ch, montage={raw.get_montage() is not None}"
    check("读取 EEGLAB 示例 + 电极位置", read)
    check("标准模板", lambda: len(mne.channels.make_standard_montage("standard_1020").ch_names))
    check("滤波", lambda: single.apply_filters(st["raw"], 50, False, 0.1, 30, log=lambda m: None) and "ok")
    check("PSD 图", lambda: type(single.plot_psd(st["raw"], 0, 60)).__name__)
    check("PSD 拓扑图", lambda: type(single.plot_psd_topo(st["raw"], 0, 60)).__name__)

    def ica():
        ic, r = single.fit_ica(st["raw"], 10, "fastica", 1.0, 97, log=lambda m: None)
        st["ica"] = ic
        figs = ic.plot_components(show=False)
        return f"{ic.n_components_} comps, {len(figs) if isinstance(figs, list) else 1} fig"
    check("ICA", ica)

    def epochs():
        ep = single.make_epochs(st["raw"], ["square", "rt"], -1, 2, (-0.5, 0), 200, None, True,
                                log=lambda m: None)
        st["ep"] = ep
        ev = ep["square"].average()
        ev.plot_joint(show=False)
        ev.plot_topomap(times=[0.5], show=False)
        return f"{len(ep)} epochs"
    check("分段 + ERP 图", epochs)

    def tfr():
        p, itc = single.compute_tfr(st["ep"]["square"], 4, 30, 6, 2, log=lambda m: None)
        p.plot_topo(baseline=(-0.5, 0), mode="logratio", show=False)
        return str(p.data.shape)
    check("时频", tfr)

    def browser():
        b = st["raw"].plot(show=False, block=False)
        name = type(b).__name__
        b.close()
        return name
    check("Qt 波形浏览器", browser)

    tmp = os.path.join(os.path.dirname(os.path.abspath(out_path)), "selftest_demo")

    def ch3():
        demo.make_demo_data1(tmp, n_subs=6, n_ch=60, n_trials=10, log=lambda m: None)
        fs = lambda c: [os.path.join(tmp, f"sub{i}_{c}.mat") for i in range(1, 7)]  # noqa: E731
        gd = group.load_group([group.Condition("F", fs("first"), "data"),
                               group.Condition("R", fs("rep"), "data")], "ch_times_trials", 250, -0.5,
                              log=lambda m: None)
        t, e = group.group_erp(gd, [49], (-0.2, 1.0), (-0.1, 0))
        r = stats.run_time_test(list(e.values()), "cluster 置换检验（配对，双侧）", n_iter=100,
                                log=lambda m: None)
        fr, tt, tf = group.group_tfr(gd, [49], np.arange(4, 31, 4.0), 2, (-0.1, 0), (-0.2, 1.0),
                                     log=lambda m: None)
        m = group.tfr_cluster(tf["F"], 0.05, 0.05, 50)
        group.plot_tfr_map(tf["F"].mean(0), fr, tt, m)
        return f"erp sig={int(np.sum(r['sig'] != 0))}, tfr {tf['F'].shape}"
    check("第 3 章组 ERP/时频 + cluster", ch3)

    def ch4():
        rng = np.random.default_rng(0)
        lab = np.tile(np.arange(8), 12)
        data = rng.normal(size=(6, lab.size, 8, 40))
        for s in range(6):
            data[s, :, :2, 15:] += np.c_[np.cos(lab * np.pi / 4), np.sin(lab * np.pi / 4)][:, :, None]
        dd = advanced.DecodingData(data, np.tile(lab, (6, 1)), [str(i) for i in range(8)], 100.0, -0.1)
        acc = advanced.tbyt_decoding(dd, 4, 5, 5, 3, 1)
        acc2 = advanced.ct_decoding(dd, 4, 10, 10, 3, 1)
        rd, r, n = advanced.rsa(dd, advanced.model_rdm("环形距离（如朝向、位置）", 8), 5, 5)
        t, mae, null = advanced.iem(dd, 10, 4, True, 100)
        return f"tbyt {acc.shape}, ct {acc2.shape}, rsa {r.shape}, iem {mae.shape}"
    check("第 4 章解码/RSA/IEM", ch4)

    ok = all(line.startswith("OK") for line in lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + f"\n\n{'全部通过' if ok else '有失败项'}\n")
    app.quit()
    return 0 if ok else 1
