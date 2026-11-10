# 脑电分析软件 (eeg-analyzer)

把 EEG Handbook（MNE-Python 教程）所有章节的分析方法，整合成一个按分析流程组织的 Windows 桌面软件（深色 / 亮色界面可切换）：

- **项目**：导入整个数据文件夹（按 被试/条件/文件 自动整理），或单个文件；一个被试可以有多条记录。支持原始数据（EDF/BDF/BrainVision/EEGLAB/CNT/GDF/FIF…）、MNE 分段数据和手册格式的 .mat 数组
- **预处理**：只处理连续数据，不需要事件。自动识别非脑电通道、自动检测坏导、滤波、重参考、ICA（没有眼电通道时用前额电极识别眼电成分）
- **事件与分段**：事件和数据分开保存，可以从任意表格导入、从通道读取、按固定间隔生成、手动输入或在波形上标记，支持通配符改名、平移；按事件或固定长度分段
- **ERP / 时频 / 解码 / RSA**：每种分析都能对全组或单个被试运行，统计检验（t 检验、置换检验、F 检验、重复测量方差分析，Bonferroni / FDR / cluster 校正）内置在分析里
- **报告**：所有结果自动汇总，一键导出 HTML 报告、图片和数据

所有计算都在本机完成，不上传数据。

## 下载使用

到 [Releases](../../releases) 下载 `eeg-analyzer-*-windows-x64.zip`，解压后双击 `脑电分析软件.exe`。整个文件夹要放在一起。

## 从源码运行

需要 Python 3.13：

```
pip install -r requirements.txt
python main.py
```

自检（逐项调用各章用到的库）：`python main.py --selftest 结果.txt`

打包成 exe：`python build_exe.py`（输出目录可用环境变量 `EEG_DIST` 修改）。

## 详细说明

见 [使用说明.md](使用说明.md)：完整流程、与手册各章的对应关系、注意事项。

`sample_data/` 是 EEGLAB 教程自带的示例数据（eeglab_data.set / .fdt 与 eeglab_chan32.locs），用于“手册示例数据”。
