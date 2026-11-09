"""生成第 3、4 章的合成演示数据（格式与手册的 demo_data1 / demo_data2 相同）。

这些是程序生成的模拟信号，只用于熟悉软件操作，不是真实被试数据。
"""
import os

import numpy as np


def make_demo_data1(folder, n_subs=8, n_ch=70, n_trials=30, sfreq=250, seed=0, log=print):
    """手册第 3 章格式：subN_first.mat / subN_rep.mat，变量 data 为 通道×时间×试次（µV），
    −0.5 到 1.5 s。第 50 个电极上，Repetition 条件的 N400 样成分更小、alpha 抑制更弱。"""
    import scipy.io as sio
    rng = np.random.default_rng(seed)
    os.makedirs(folder, exist_ok=True)
    t = -0.5 + np.arange(500) / sfreq
    for s in range(n_subs):
        for cond, amp, alpha_drop in (("first", -6.0, 0.6), ("rep", -3.0, 0.25)):
            data = rng.normal(0, 4, (n_ch, 500, n_trials))
            erp = amp * np.exp(-((t - 0.4) / 0.08) ** 2) + 3 * np.exp(-((t - 0.17) / 0.04) ** 2)
            spatial = np.exp(-((np.arange(n_ch) - 49) / 8.0) ** 2)
            data += (spatial[:, None] * erp[None])[:, :, None] * (1 + 0.2 * rng.normal())
            env = 1 - alpha_drop * (t > 0.2) * (t < 0.9)
            for k in range(n_trials):
                ph = rng.uniform(0, 2 * np.pi)
                data[:, :, k] += spatial[:, None] * (3 * env * np.sin(2 * np.pi * 10 * t + ph))[None]
            sio.savemat(os.path.join(folder, f"sub{s + 1}_{cond}.mat"), {"data": data.astype(np.float32)})
        log(f"第 3 章演示数据：被试 {s + 1}/{n_subs}")


def make_demo_data2(folder, subs=("201", "202", "203", "204", "205"), n_trials=320, n_ch=27,
                    sfreq=250, seed=1, log=print):
    """手册第 4 章格式：data/ERPxxx.mat（filtData：试次×通道×时间，−1.5 到 1.5 s）与
    labels/ori_xxx.txt、pos_xxx.txt（第 1 列角度，第 2 列 0–15 类别）。"""
    import scipy.io as sio
    rng = np.random.default_rng(seed)
    os.makedirs(os.path.join(folder, "data"), exist_ok=True)
    os.makedirs(os.path.join(folder, "labels"), exist_ok=True)
    t = -1.5 + np.arange(750) / sfreq
    w_ori = rng.normal(size=(n_ch, 2))
    w_pos = rng.normal(size=(n_ch, 2))
    for sub in subs:
        ori = np.tile(np.arange(16), n_trials // 16)
        pos = rng.permutation(np.tile(np.arange(16), n_trials // 16))
        rng.shuffle(ori)
        data = rng.normal(0, 3, (n_trials, n_ch, 750))
        # 朝向信息 0.1–0.5 s 出现，位置信息 0.1–1.2 s 持续
        g_ori = np.exp(-((t - 0.25) / 0.12) ** 2)
        g_pos = 1 / (1 + np.exp(-(t - 0.1) / 0.03)) * (1 / (1 + np.exp((t - 1.2) / 0.1)))
        for k in range(n_trials):
            a, b = 2 * np.pi * ori[k] / 16, 2 * np.pi * pos[k] / 16
            data[k] += 1.2 * (w_ori @ [np.cos(a), np.sin(a)])[:, None] * g_ori[None]
            data[k] += 1.2 * (w_pos @ [np.cos(b), np.sin(b)])[:, None] * g_pos[None]
        sio.savemat(os.path.join(folder, "data", f"ERP{sub}.mat"), {"filtData": data.astype(np.float32)})
        np.savetxt(os.path.join(folder, "labels", f"ori_{sub}.txt"), np.c_[ori * 22.5, ori], fmt="%g")
        np.savetxt(os.path.join(folder, "labels", f"pos_{sub}.txt"), np.c_[pos * 22.5, pos], fmt="%g")
        log(f"第 4 章演示数据：被试 {sub}")


def make_all(folder, log=print):
    make_demo_data1(os.path.join(folder, "demo_data1"), log=log)
    make_demo_data2(os.path.join(folder, "demo_data2"), log=log)
    with open(os.path.join(folder, "说明.txt"), "w", encoding="utf-8") as f:
        f.write("本文件夹是脑电分析软件生成的合成演示数据（模拟信号），格式与 EEG Handbook 的 demo_data1、"
                "demo_data2 相同，只用于熟悉软件操作，不是真实被试数据，不能作为研究结果。\n")
    return folder
