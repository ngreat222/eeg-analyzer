# 脑电分析软件 (eeg-analyzer)

把 EEG Handbook（MNE-Python 教程）的分析流程做成 Windows 本机运行的桌面程序：

- **第 1 章 单被试**：读取（EDF/BDF/BrainVision/EEGLAB/CNT/GDF/FIF…）→ 电极定位 → 滤波 → 坏导 → ICA → 分段 → ERP → 时频 → 导出
- **第 3 章 多被试**：组 ERP、组时频，统计方法来自第 2 章（t 检验、置换检验、F 检验、两因素重复测量方差分析，Bonferroni / FDR / cluster 校正）
- **第 4 章 高级分析**：逐时间点解码、跨时间解码、RSA、IEM

所有计算都在本机完成，不上传数据。

## 下载使用

到 [Releases](../../releases) 下载 `脑电分析软件-*.zip`，解压后双击 `脑电分析软件.exe`。整个文件夹要放在一起。

## 从源码运行

需要 Python 3.13：

```
pip install -r requirements.txt
python main.py
```

自检（逐项调用各章用到的库）：`python main.py --selftest 结果.txt`

打包成 exe：`python build_exe.py`（输出目录可用环境变量 `EEG_DIST` 修改）。

## 详细说明

见 [使用说明.md](使用说明.md)：各步骤与手册单元的对应关系、从单被试到多被试的流程、注意事项。

`sample_data/` 是 EEGLAB 教程自带的示例数据（eeglab_data.set / .fdt 与 eeglab_chan32.locs），用于“打开手册示例数据”。
