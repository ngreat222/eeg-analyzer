"""第 2 章的统计方法，作用在 [被试, 时间] 或 [被试, 频率, 时间] 的组数据上。"""
import numpy as np
from mne.stats import fdr_correction, f_mway_rm
from scipy.stats import f_oneway, ttest_1samp, ttest_ind, ttest_rel

# 名称 → 适用的条件数
TESTS_1 = ["单样本 t 检验（与 0 比较）", "置换检验（与 0 比较）",
           "cluster 置换检验（单侧，> 0）", "cluster 置换检验（双侧）"]
TESTS_2 = ["配对 t 检验", "独立样本 t 检验", "置换检验（配对）", "cluster 置换检验（配对，双侧）"]
TESTS_N = ["单因素 F 检验", "重复测量方差分析（两因素）"]
# 组水平（被试为单位，条件之间配对）与单被试（试次为单位，条件之间独立）各自适用的检验
GROUP_TESTS = {1: TESTS_1, 2: ["配对 t 检验", "置换检验（配对）", "cluster 置换检验（配对，双侧）"],
               "n": TESTS_N}
TRIAL_TESTS = {1: TESTS_1, 2: ["独立样本 t 检验", "置换检验（独立样本）", "cluster 置换检验（独立样本）"],
               "n": ["单因素 F 检验"]}


def tests_for(k, level="group"):
    table = GROUP_TESTS if level == "group" else TRIAL_TESTS
    return table.get(k, table["n"]) if k >= 1 else []
CORRECTIONS = ["不校正", "Bonferroni", "FDR"]


def permutation_test(x, y, n_iter=1000, rng=None, paired=None):
    """两样本置换检验（双侧，配对时交换符号）。对应手册里 NeuroRA 1.x 的 permutation_test()。"""
    rng = np.random.default_rng(rng)
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if paired is None:
        paired = x.shape == y.shape
    if paired:
        d = x - y
        obs = abs(d.mean())
        signs = rng.choice([-1, 1], size=(n_iter, d.size))
        null = np.abs((signs * d).mean(axis=1))
    else:
        allv = np.concatenate([x, y])
        obs = abs(x.mean() - y.mean())
        null = np.empty(n_iter)
        for i in range(n_iter):
            p = rng.permutation(allv)
            null[i] = abs(p[:x.size].mean() - p[x.size:].mean())
    return (np.sum(null >= obs) + 1) / (n_iter + 1)


def correct(p, method, alpha=0.05):
    p = np.asarray(p, float)
    p = np.where(np.isnan(p), 1.0, p)  # 各条件完全相同时 F/t 为 0/0
    if method == "Bonferroni":
        return np.minimum(p * p.size, 1.0)
    if method == "FDR":
        return fdr_correction(p, alpha=alpha)[1]
    return p


def _cluster_1d(x, tail, p_threshold, clusterp, n_iter):
    """x: [被试, 时间]，与 0 比较。返回 0/1/-1 掩码。"""
    from neurora.stuff import (clusterbased_permutation_1d_1samp_1sided,
                               clusterbased_permutation_1d_1samp_2sided)
    warn_small_n(x.shape[0], clusterp)
    if tail == 1:
        return clusterbased_permutation_1d_1samp_1sided(
            x, level=0, p_threshold=p_threshold, clusterp_threshold=clusterp, n_iter=n_iter)
    return clusterbased_permutation_1d_1samp_2sided(
        x, level=0, p_threshold=p_threshold, clusterp_threshold=clusterp, n_iter=n_iter)


def run_time_test(conds, test, correction="不校正", alpha=0.05, n_iter=1000,
                  factor_levels=None, cluster_p=0.05, log=print):
    """对 [被试, 时间] 的若干条件做逐时间点检验。

    返回 dict：sig（布尔或 ±1 数组，或两因素时 {效应名: 布尔数组}）、p（未校正 p）、p_corr、stat。
    """
    k = len(conds)
    n_t = conds[0].shape[1]
    out = dict(test=test, correction=correction)
    if k == 1:
        x = conds[0]
        if test.startswith("单样本"):
            stat, p = ttest_1samp(x, 0, axis=0)
        elif test.startswith("置换"):
            stat = x.mean(0)
            p = np.array([permutation_test(x[:, t], np.zeros(x.shape[0]), n_iter) for t in range(n_t)])
        elif test.startswith("cluster"):
            tail = 1 if "单侧" in test else 2
            mask = _cluster_1d(x, tail, alpha, cluster_p, n_iter)
            out.update(sig=np.asarray(mask), p=None, p_corr=None, stat=None)
            log(f"{test}：显著时间点 {int(np.sum(mask != 0))} 个")
            return out
        else:
            raise ValueError(f"一个条件不能用 {test}")
    elif k == 2:
        a, b = conds
        if test == "配对 t 检验":
            _check_paired(a, b)
            stat, p = ttest_rel(a, b, axis=0)
        elif test == "独立样本 t 检验":
            stat, p = ttest_ind(a, b, axis=0)
        elif test == "置换检验（独立样本）":
            stat = a.mean(0) - b.mean(0)
            p = np.array([permutation_test(a[:, t], b[:, t], n_iter, paired=False) for t in range(n_t)])
        elif test.startswith("置换"):
            _check_paired(a, b)
            stat = a.mean(0) - b.mean(0)
            p = np.array([permutation_test(a[:, t], b[:, t], n_iter, paired=True) for t in range(n_t)])
        elif test == "cluster 置换检验（独立样本）":
            from mne.stats import permutation_cluster_test
            _, clusters, cp, _ = permutation_cluster_test([a, b], n_permutations=n_iter, out_type="mask",
                                                          verbose=False)
            mask = np.zeros(n_t)
            diff = a.mean(0) - b.mean(0)
            for c, pv in zip(clusters, cp):
                if pv < cluster_p:
                    mask[c] = np.sign(diff[c].mean()) or 1
            out.update(sig=mask, p=None, p_corr=None, stat=None)
            log(f"{test}：显著时间点 {int(np.sum(mask != 0))} 个")
            return out
        elif test.startswith("cluster"):
            _check_paired(a, b)
            mask = _cluster_1d(a - b, 2, alpha, cluster_p, n_iter)
            out.update(sig=np.asarray(mask), p=None, p_corr=None, stat=None)
            log(f"{test}：显著时间点 {int(np.sum(mask != 0))} 个")
            return out
        else:
            raise ValueError(f"两个条件不能用 {test}")
    else:
        if test == "单因素 F 检验":
            stat, p = f_oneway(*conds, axis=0)
        elif test.startswith("重复测量"):
            levels = factor_levels or [2, k // 2]
            if int(np.prod(levels)) != k:
                raise ValueError(f"因素水平 {levels} 的乘积应等于条件数 {k}")
            for c in conds[1:]:
                _check_paired(conds[0], c)
            data = np.stack(conds, axis=1)  # [被试, 条件, 时间]
            res = {}
            for eff, name in (("A", "因素 A 主效应"), ("B", "因素 B 主效应"), ("A:B", "交互作用")):
                f, pv = f_mway_rm(data, levels, effects=eff)
                pc = correct(pv, correction, alpha)
                res[name] = dict(stat=f, p=pv, p_corr=pc, sig=pc < alpha)
                log(f"{name}：显著时间点 {int(np.sum(pc < alpha))} 个")
            out.update(effects=res)
            return out
        else:
            raise ValueError(f"{k} 个条件不能用 {test}")

    p = np.asarray(p, float)
    pc = correct(p, correction, alpha)
    out.update(stat=np.asarray(stat), p=p, p_corr=pc, sig=pc < alpha)
    log(f"{test}（{correction}）：显著时间点 {int(np.sum(pc < alpha))}/{n_t} 个")
    return out


def warn_small_n(n_subs, cluster_p, log=print):
    """MNE 的单样本 cluster 置换检验在被试很少时做精确检验，最小可能 p = 1 / 2^(n-1)。"""
    min_p = 1 / 2 ** (n_subs - 1)
    if min_p >= cluster_p:
        log(f"⚠ 只有 {n_subs} 个被试：cluster 置换检验能得到的最小 p = 1/2^{n_subs - 1} = {min_p:.4f}，"
            f"不可能小于 {cluster_p}，所以不会有显著区域。可以改用逐点 t 检验，或增加被试。"
            f"（手册用的旧版 NeuroRA 置换方法不同，5 个被试时也能标出显著区间。）")
        return True
    return False


def _check_paired(a, b):
    if a.shape[0] != b.shape[0]:
        raise ValueError(f"配对检验要求两个条件的被试数相同（现在是 {a.shape[0]} 和 {b.shape[0]}）")


def sem(x, axis=0, ddof=0):
    return np.std(x, axis=axis, ddof=ddof) / np.sqrt(x.shape[axis])
