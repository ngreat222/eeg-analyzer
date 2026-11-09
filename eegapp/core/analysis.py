"""统一的分析入口：每种分析都可以对一个被试（以试次为单位）或全组（以被试为单位）运行，
统计检验、指标和图一起生成。原手册第 1–4 章的方法都在这里汇合。
"""
from dataclasses import dataclass, field

import mne
import numpy as np
from matplotlib.figure import Figure
from scipy import stats as sps

from . import advanced, group, stats
from .single import parse_names

GROUP = "全组"


@dataclass
class Result:
    kind: str
    title: str
    figures: list = field(default_factory=list)     # [(标题, Figure)]
    metrics: list = field(default_factory=list)     # [(名称, 数值文字, 说明)]
    table: tuple = None                             # (表头, 行)
    arrays: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    table_title: str = "每名被试的数值"


def _subjects(project, scope):
    done = project.done_subjects()
    if not done:
        raise ValueError("项目里还没有预处理好的被试。请先在「项目」导入数据，或在「预处理」处理原始数据。")
    if scope == GROUP:
        return [(s.name, project.epochs(s.name)) for s in done]
    return [(scope, project.epochs(scope))]


def _picks(ep, channels):
    names = parse_names(channels)
    eeg = [ep.ch_names[i] for i in mne.pick_types(ep.info, eeg=True)]
    if not names:
        for c in ("Cz", "Pz", "CPz", "FCz"):
            if c in eeg:
                return [c]
        return eeg[:1]
    low = {c.lower(): c for c in ep.ch_names}
    out = []
    for n in names:
        if n.isdigit() and 0 < int(n) <= len(eeg):
            out.append(eeg[int(n) - 1])
        elif n.lower() in low:
            out.append(low[n.lower()])
        else:
            raise ValueError(f"找不到通道 “{n}”")
    return out


def _fmt_p(p):
    if p is None or np.isnan(p):
        return "—"
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def _window(times, w):
    idx = np.where((times >= w[0] - 1e-9) & (times <= w[1] + 1e-9))[0]
    if idx.size == 0:
        raise ValueError(f"时间段 {w[0]}–{w[1]} s 超出数据范围（{times[0]:.2f}–{times[-1]:.2f} s）")
    return idx


def _sig_text(sig, times):
    sig = np.asarray(sig) != 0
    if not sig.any():
        return "无显著时间段"
    segs, start = [], None
    for i, v in enumerate(sig):
        if v and start is None:
            start = i
        if (not v or i == len(sig) - 1) and start is not None:
            end = i if v else i - 1
            a, b = times[start] * 1000, times[end] * 1000
            segs.append(f"{a:.0f} ms" if end == start else f"{a:.0f}–{b:.0f} ms")
            start = None
    return "，".join(segs[:4]) + (" 等" if len(segs) > 4 else "")


def _compare_metrics(vals, paired):
    """vals: {条件: 一维数组}。返回 [(名称, 数值, 说明)]。"""
    m = []
    for c, v in vals.items():
        m.append((f"{c} 平均振幅", f"{v.mean():.2f} µV", f"±{stats.sem(v):.2f} SEM，n={len(v)}"))
    if len(vals) == 2:
        (a_n, a), (b_n, b) = vals.items()
        if paired:
            t, p = sps.ttest_rel(a, b)
            d = (a - b).mean() / ((a - b).std(ddof=1) or np.nan)
            lab = "配对 t 检验"
        else:
            t, p = sps.ttest_ind(a, b)
            sp_ = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
            d = (a.mean() - b.mean()) / (sp_ or np.nan)
            lab = "独立样本 t 检验"
        m.append((f"差异 {a_n} − {b_n}", f"{a.mean() - b.mean():.2f} µV", f"{lab} t={t:.2f}"))
        m.append(("p 值", _fmt_p(p), "时间窗内平均振幅"))
        m.append(("效应量 d", f"{d:.2f}", "Cohen's d" + ("z" if paired else "")))
    return m


# ====================================================================== ERP
def erp(project, scope, conditions, channels, win, metric_win, test, correction, alpha=0.05,
        n_iter=1000, cluster_p=0.05, factor_levels=None, topo_times="auto", log=print):
    subs = _subjects(project, scope)
    if not conditions:
        raise ValueError("请至少选择一个条件")
    level = "group" if scope == GROUP and len(subs) >= 2 else "trial"
    ep0 = subs[0][1]
    picks = _picks(ep0, channels)
    ch_label = "+".join(picks) if len(picks) <= 3 else f"{len(picks)} 个通道平均"
    res = Result("ERP", f"ERP · {ch_label} · {'全组 ' + str(len(subs)) + ' 人' if level == 'group' else subs[0][0]}")
    res.params = dict(范围=scope, 条件=", ".join(conditions), 通道=", ".join(picks), 显示=f"{win[0]}–{win[1]} s",
                      指标时间窗=f"{metric_win[0]}–{metric_win[1]} s", 检验=test, 校正=correction)

    times = ep0.times
    w = _window(times, win)
    mw = _window(times, metric_win)
    curves, evokeds = {}, {c: [] for c in conditions}
    if level == "group":
        for name, ep in subs:
            for c in conditions:
                ev = ep[c].average()
                evokeds[c].append(ev)
        for c in conditions:
            curves[c] = np.array([ev.copy().pick(picks).data.mean(0) * 1e6 for ev in evokeds[c]])
    else:
        ep = subs[0][1]
        for c in conditions:
            curves[c] = ep[c].get_data(picks=picks).mean(1) * 1e6
            evokeds[c] = [ep[c].average()]
    if test and test != "不做检验":
        res_stat = stats.run_time_test([curves[c][:, w] for c in conditions], test, correction, alpha, n_iter,
                                       factor_levels, cluster_p, log=log)
    else:
        res_stat = None
    unit = "被试" if level == "group" else "试次"
    title = f"{ch_label}（阴影 = ±1 SEM，n 为{unit}数）"
    res.figures.append(("ERP 波形与统计", group.plot_group_erp(times[w], {c: curves[c][:, w] for c in conditions},
                                                             res_stat, None, title)))
    if res_stat is not None:
        if "effects" in res_stat:
            for k, v in res_stat["effects"].items():
                res.metrics.append((k, _sig_text(v["sig"], times[w]), test))
        else:
            res.metrics.append(("显著时间段", _sig_text(res_stat["sig"], times[w]),
                                test + ("" if test.startswith("cluster") or correction == "不校正"
                                        else f"，{correction} 校正")))
    res.metrics += _compare_metrics({c: curves[c][:, mw].mean(1) for c in conditions}, level == "group")

    # 总平均与地形图
    grand = {c: (mne.grand_average(evokeds[c]) if len(evokeds[c]) > 1 else evokeds[c][0]) for c in conditions}
    has_mont = ep0.get_montage() is not None
    ga = grand[conditions[0]]
    if has_mont:
        tt = "peaks" if topo_times in ("", "auto") else [float(x) for x in parse_names(topo_times)]
        try:
            res.figures.append((f"{conditions[0]} 联合图", ga.plot_joint(times=tt, show=False,
                                                                     title=f"{conditions[0]} 总平均")))
        except Exception as e:  # noqa: BLE001
            log(f"联合图未生成：{e}")
        if len(conditions) == 2:
            diff = mne.combine_evoked([grand[conditions[0]], grand[conditions[1]]], weights=[1, -1])
            fig = diff.plot_topomap(times=[np.mean(metric_win)], average=metric_win[1] - metric_win[0],
                                    show=False, colorbar=True)
            fig.set_size_inches(5, 4.2)
            fig.suptitle(f"差异地形图 {conditions[0]} − {conditions[1]}（{metric_win[0]}–{metric_win[1]} s）")
            res.figures.append(("差异地形图", fig))
        res.figures.append(("各条件对比", mne.viz.plot_compare_evokeds(
            {c: evokeds[c] for c in conditions} if level == "group" else grand,
            picks=picks, combine="mean" if len(picks) > 1 else None, show=False,
            title=f"各条件 ERP（{ch_label}）")[0]))
    else:
        res.notes.append("数据没有电极位置，未画地形图。")
    if level == "trial":
        try:
            figs = subs[0][1][conditions[0]].plot_image(picks=picks, combine="mean" if len(picks) > 1 else None,
                                                        show=False, title=f"{conditions[0]} 单试次图像")
            res.figures.append(("单试次图像", figs[0]))
        except Exception as e:  # noqa: BLE001
            log(f"单试次图像未生成：{e}")
    res.arrays = dict(times=times[w], **{f"erp_{c}": curves[c][:, w] for c in conditions})
    if res_stat is not None and "sig" in res_stat:
        res.arrays["significant"] = np.asarray(res_stat["sig"], float)
    if level == "group":
        rows = [[n] + [f"{curves[c][i, mw].mean():.3f}" for c in conditions] for i, (n, _) in enumerate(subs)]
        res.table = (["被试"] + [f"{c}（µV）" for c in conditions], rows)
        res.table_title = f"每名被试 {metric_win[0]}–{metric_win[1]} s 平均振幅"
    return res


# ====================================================================== 时频与频谱
BANDS = {"θ 4–8 Hz": (4, 8), "α 8–12 Hz": (8, 12), "β 12–30 Hz": (12, 30)}


def tfr(project, scope, conditions, channels, fmin, fmax, fstep, cycles_div, baseline, win, metric_band,
        metric_win, do_stats=True, p=0.05, cluster_p=0.05, n_iter=500, decim=1, log=print):
    subs = _subjects(project, scope)
    if len(conditions) not in (1, 2):
        raise ValueError("时频分析请选择 1 个或 2 个条件")
    level = "group" if scope == GROUP and len(subs) >= 2 else "trial"
    ep0 = subs[0][1]
    picks = _picks(ep0, channels)
    ch_label = "+".join(picks) if len(picks) <= 3 else f"{len(picks)} 个通道平均"
    freqs = np.arange(fmin, fmax + fstep / 2, fstep)
    n_cycles = freqs / cycles_div
    res = Result("时频", f"时频 · {ch_label} · {'全组 ' + str(len(subs)) + ' 人' if level == 'group' else subs[0][0]}")
    res.params = dict(范围=scope, 条件=", ".join(conditions), 通道=", ".join(picks),
                      频率=f"{fmin:g}–{fmax:g} Hz 步长 {fstep:g}", n_cycles=f"频率/{cycles_div:g}",
                      基线=f"{baseline[0]}–{baseline[1]} s（dB）")
    maps = {}
    times = None
    for c in conditions:
        rows = []
        for i, (name, ep) in enumerate(subs):
            e = ep[c].copy().pick(picks)
            pw = e.compute_tfr("morlet", freqs=freqs, n_cycles=n_cycles, use_fft=True, decim=decim,
                               average=level == "group", return_itc=False, verbose=False)
            data = pw.data  # group: [ch,f,t]；trial: [trials,ch,f,t]
            times = pw.times
            b = _window(times, baseline)
            if level == "group":
                d = data.mean(0)
                rows.append(10 * np.log10(d / d[:, b].mean(1, keepdims=True)))
            else:
                d = data.mean(1)  # [trials, f, t]
                base = d.mean(0)[:, b].mean(1)[None, :, None]   # 以平均功率的基线为参考
                rows.extend(list(10 * np.log10(d / base)))
            log(f"时频 {c}：{i + 1}/{len(subs)}")
        maps[c] = np.array(rows)
    w = _window(times, win)
    for c in conditions:
        maps[c] = maps[c][:, :, w]
    tw = times[w]
    masks = {}
    for c in conditions:
        m = group.tfr_cluster(maps[c], p, cluster_p, n_iter) if do_stats else None
        masks[c] = m
        lim = np.nanpercentile(np.abs(maps[c].mean(0)), 98) or 1
        res.figures.append((f"{c} 时频图", group.plot_tfr_map(maps[c].mean(0), freqs, tw, m, (-lim, lim),
                                                            f"{c}（{ch_label}，相对基线 dB）")))
    if len(conditions) == 2:
        a, b2 = maps[conditions[0]], maps[conditions[1]]
        if level == "group":
            m = group.tfr_cluster(a, p, cluster_p, n_iter, other=b2) if do_stats else None
        else:
            m = None
            res.notes.append("单个被试的两条件差异图没有做统计检验（试次之间不配对）。")
        masks["diff"] = m
        dmean = a.mean(0) - b2.mean(0)
        lim = np.nanpercentile(np.abs(dmean), 98) or 1
        res.figures.append(("差异时频图", group.plot_tfr_map(dmean, freqs, tw, m, (-lim, lim),
                                                          f"{conditions[0]} − {conditions[1]}", cbar="ΔdB")))
    if do_stats:
        res.notes.append("虚线圈出 cluster 置换检验显著的区域：黄色 = 高于基线 / 条件 1 更高，绿色 = 低于。")
    fb = (freqs >= metric_band[0]) & (freqs <= metric_band[1])
    mwi = _window(tw, metric_win)
    vals = {c: maps[c][:, fb][:, :, mwi].mean(axis=(1, 2)) for c in conditions}
    for c, v in vals.items():
        tt, pp = sps.ttest_1samp(v, 0)
        res.metrics.append((f"{c} {metric_band[0]:g}–{metric_band[1]:g} Hz", f"{v.mean():+.2f} dB",
                            f"{metric_win[0]}–{metric_win[1]} s；与基线比 p {_fmt_p(pp)}"))
    if len(conditions) == 2:
        a, b2 = vals[conditions[0]], vals[conditions[1]]
        tt, pp = sps.ttest_rel(a, b2) if level == "group" else sps.ttest_ind(a, b2)
        res.metrics.append(("条件差异", f"{(a.mean() - b2.mean()):+.2f} dB", f"p {_fmt_p(pp)}"))
    # 频段功率地形图（总平均）
    if ep0.get_montage() is not None:
        try:
            res.figures.append(("频段功率地形图", band_topomaps(subs, conditions)))
        except Exception as e:  # noqa: BLE001
            log(f"频段地形图未生成：{e}")
    res.arrays = dict(freqs=freqs, times=tw, **{f"tfr_{c}": maps[c] for c in conditions},
                      **{f"mask_{k}": v for k, v in masks.items() if v is not None})
    return res


def band_topomaps(subs, conditions):
    ep0 = subs[0][1]
    info = ep0.copy().pick("eeg").info
    fig = Figure(figsize=(3 * len(BANDS) + 1, 2.8 * len(conditions)), layout="constrained")
    axes = np.atleast_2d(fig.subplots(len(conditions), len(BANDS)))
    for i, c in enumerate(conditions):
        powers = {b: [] for b in BANDS}
        for _, ep in subs:
            sp = ep[c].compute_psd(fmin=1, fmax=40, picks="eeg", verbose=False).average()
            data, fr = sp.get_data(return_freqs=True)
            for b, (lo, hi) in BANDS.items():
                powers[b].append(10 * np.log10(data[:, (fr >= lo) & (fr < hi)].mean(1) * 1e12))
        for j, b in enumerate(BANDS):
            v = np.mean(powers[b], axis=0)
            im, _ = mne.viz.plot_topomap(v, info, axes=axes[i, j], show=False, cmap="magma", contours=0)
            axes[i, j].set_title(f"{c} · {b}", fontsize=10)
        fig.colorbar(im, ax=axes[i, :].tolist(), shrink=0.8, label="dB (µV²/Hz)")
    return fig


# ====================================================================== 解码 / IEM / RSA
def decoding_data(project, scope, label, classes, log=print):
    subs = _subjects(project, scope)
    per_sub = []
    for name, ep in subs:
        if label == "条件":
            inv = {v: k for k, v in ep.event_id.items()}
            lab = np.array([inv[e] for e in ep.events[:, 2]])
        else:
            lab = ep.metadata[label].astype(str).to_numpy()
        keep = np.isin(lab, classes)
        per_sub.append((ep.get_data(picks="eeg")[keep] * 1e6, lab[keep]))
    counts = [[int(np.sum(lab == c)) for c in classes] for _, lab in per_sub]
    n_min = int(np.min(counts))
    if n_min < 2:
        raise ValueError(f"有被试的某个类别只有 {n_min} 个试次，无法解码。")
    data, labels = [], []
    for d, lab in per_sub:
        idx = np.concatenate([np.where(lab == c)[0][:n_min] for c in classes])
        idx.sort()
        data.append(d[idx])
        labels.append(np.array([classes.index(x) for x in lab[idx]]))
    log(f"每个被试每类取 {n_min} 个试次（按最少的对齐）")
    ep0 = subs[0][1]
    return advanced.DecodingData(np.stack(data), np.stack(labels), list(classes), ep0.info["sfreq"], ep0.tmin,
                                 label)


def decode(dd, method, navg, time_win, time_step, nfolds, nrepeats, use_cluster=True, p=0.05, cluster_p=0.05,
           n_iter=1000, stats_time=(0, 1), circular=True, num_perm=2000, n_jobs=1, log=print):
    n_sub = dd.data.shape[0]
    res = Result("解码", f"{method} · {dd.label_name} · {n_sub} 人")
    res.params = dict(标签=dd.label_name, 类别=", ".join(dd.class_names), 方法=method, navg=navg,
                      时间窗=time_win, 步长=time_step, 折数=nfolds, 重复=nrepeats)
    chance = 1 / dd.n_classes
    if method == "逐时间点解码":
        acc = advanced.tbyt_decoding(dd, navg, time_win, time_step, nfolds, nrepeats, n_jobs=n_jobs)
        t = advanced.window_times(dd, acc.shape[1], time_win, time_step)
        mask = (advanced.stats_mask_1d(acc, chance, t, stats_time, p, cluster_p, n_iter, use_cluster)
                if n_sub >= 2 else None)
        res.figures.append(("解码正确率", advanced.plot_timecourse(acc, t, mask, chance,
                                                              title=f"{dd.label_name}：逐时间点解码")))
        m = acc.mean(0)
        k = int(np.argmax(m))
        res.metrics += [("峰值正确率", f"{m[k]:.1%}", f"{t[k] * 1000:.0f} ms，机会水平 {chance:.1%}"),
                        ("显著时间段", _sig_text(mask, t) if mask is not None else "需要 2 名以上被试", "")]
        res.arrays = dict(times=t, accuracy=acc, **({"significant": mask} if mask is not None else {}))
    elif method == "跨时间泛化":
        acc = advanced.ct_decoding(dd, navg, time_win, time_step, nfolds, nrepeats, n_jobs=n_jobs)
        t = advanced.window_times(dd, acc.shape[1], time_win, time_step)
        mask = (advanced.stats_mask_2d(acc, chance, t, stats_time, p, cluster_p, n_iter)
                if n_sub >= 2 and use_cluster else None)
        res.figures.append(("跨时间泛化矩阵", advanced.plot_ct(acc.mean(0), t, mask,
                                                          title=f"{dd.label_name}：跨时间泛化")))
        diag = np.diag(acc.mean(0))
        k = int(np.argmax(diag))
        res.metrics += [("对角线峰值", f"{diag[k]:.1%}", f"{t[k] * 1000:.0f} ms，机会水平 {chance:.1%}")]
        res.arrays = dict(times=t, accuracy=acc)
    elif method == "反向编码模型 IEM":
        t, mae, null = advanced.iem(dd, time_win, max(2, min(nfolds, 10)), circular, num_perm, progress=log)
        mask, ps = advanced.iem_stats(mae, null, p) if n_sub >= 2 else (None, None)
        res.figures.append(("IEM 重建", advanced.plot_iem(t, mae, null, mask, None,
                                                       f"{dd.label_name}：IEM 重建（ΔMAE 越大越好）")))
        dm = np.mean(null) - mae.mean(0)
        k = int(np.argmax(dm))
        res.metrics += [("峰值 ΔMAE", f"{dm[k]:.2f}", f"{t[k] * 1000:.0f} ms（单位：类别步长）"),
                        ("显著时间段", _sig_text(mask, t) if mask is not None else "需要 2 名以上被试", "")]
        res.arrays = dict(times=t, mae=mae, null_mae=null)
    else:
        raise ValueError(method)
    if n_sub < 2:
        res.notes.append("只有 1 名被试，结果没有做组水平统计检验。")
    return res


def rsa(dd, model_kind, model_file, time_win, time_step, distance="correlation", use_cluster=True, p=0.05,
        cluster_p=0.05, n_iter=1000, stats_time=(0, 1), log=print):
    model = advanced.model_rdm(model_kind, dd.n_classes, model_file)
    rdms, r, n_min = advanced.rsa(dd, model, time_win, time_step, distance)
    t = advanced.window_times(dd, r.shape[1], time_win, time_step)
    n_sub = r.shape[0]
    mask = (advanced.stats_mask_1d(r, 0, t, stats_time, p, cluster_p, n_iter, use_cluster)
            if n_sub >= 2 else None)
    res = Result("RSA", f"RSA · {dd.label_name} · {n_sub} 人")
    res.params = dict(标签=dd.label_name, 模型=model_kind, 距离=distance, 时间窗=time_win, 步长=time_step)
    res.figures.append(("与模型 RDM 的相关", advanced.plot_timecourse(
        r, t, mask, None, ylabel="Spearman r", title=f"{dd.label_name}：脑电 RDM 与模型 RDM 的相关",
        color="#AFA9EC")))
    k = int(np.argmax(r.mean(0)))
    res.figures.append(("模型 RDM", advanced.plot_rdm(model, dd.class_names, f"模型 RDM：{model_kind}")))
    res.figures.append((f"脑电 RDM（{t[k] * 1000:.0f} ms）",
                        advanced.plot_rdm(rdms[:, k].mean(0), dd.class_names, f"脑电 RDM（{t[k] * 1000:.0f} ms，峰值时刻）")))
    res.metrics += [("峰值相关", f"r = {r.mean(0)[k]:.2f}", f"{t[k] * 1000:.0f} ms"),
                    ("显著时间段", _sig_text(mask, t) if mask is not None else "需要 2 名以上被试", ""),
                    ("每类试次", str(n_min), "各被试各类别取相同数量")]
    res.arrays = dict(times=t, similarity=r, rdms=rdms, model=model)
    return res
