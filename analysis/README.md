# 共享事件分析

`EventAnalysis.h` 提供三套 ROOT 宏和 Notebook 共用的读取、筛选及直方图逻辑。
`notebook_analysis.py` 负责运行快照、事件重建、拟合、Matplotlib 绘图和结果导出。
入口示例是 [真实数据 Notebook](../jupyter/02_real_run_energy_and_timing.ipynb)。

## 数据与筛选约定

输入树名为 `tr`，`m` 为 `UShort_t`；`board[m]`、`channel[m]` 为 `Short_t`，
`energy[m]` 为 `Double_t`，`timestamp[m]` 为 `Long64_t`，时间单位为 ps。
读取器使用 ROOT 管理的动态数组，检查多重数在 1–8 内之后复制到公开的向量；
原先 `ana` 的 `GetEntry`、`Loop`、`Show` 等调用仍可使用。
由调用者传入的树和文件保持调用者所有权。

多重数统计数组的长度常量使用完整命名空间，兼容 ROOT 6.34 的 PyROOT 类型绑定；
数组长度和事件数据格式保持一致，索引 0 保留，索引 1–8 记录对应事件数。

能谱统计所有事件中的命中，并以 `board0_ch2` 等键区分板号和通道。
时间差仅针对 `m == 2` 的指定通道对，固定为 `t_B - t_A`。
先做整数时间差，再转为 ns，保留大时间戳下的差值精度。
单通道的多个能量窗口取并集，两通道条件取交集，窗口边界为严格开区间。
空窗口列表表示不施加该通道能量门，板号 `-1` 表示不限板号。
计数单列直方图的上、下溢，避免缩小显示范围时隐藏入选事件。

## 拟合与解释

使用 ROOT `TH1::Fit` 的 Poisson 似然和 bin 内积分，模型为 Gaussian 加非负常数背景。
设置 `LIRSQN`，返回完整拟合结果并保留协方差；
参考 [ROOT 拟合说明](https://root.cern.ch/manual/fitting/) 及
[TH1::Fit 文档](https://root.cern.ch/doc/master/classTH1.html)。
拟合端点必须是等宽直方图的 bin 边界。

至少需要 100 个计数、8 个 bin、4 个有计数的 bin；否则记录跳过原因。
未收敛、协方差不可用或峰参数顶到边界时，不报告 FWHM。
`usable` 仅表示数值拟合及误差可以提取，不表示模型已通过物理验证。
输出 Poisson deviance、自由度和 Pearson 残差图；deviance/ndf 大于 2 时
标记 `inspect_model`，只是检查提示，不能替代残差和模型研究。

FWHM 为 `2 * sqrt(2 * ln(2)) * sigma`，误差来自该模型的拟合协方差。
它不包含背景模型、峰尾、区间、能量门、分箱和历史刻度的不确定度。
Notebook 会对几个时间拟合区间重复拟合，但不把其跨度当作系统误差估计。
时间 FWHM 描述两个通道的时间差，未换算成单探测器分辨率。
能量单位在历史刻度核实前保持为存储单位。

## 输出与重跑

Notebook 从已有的排序 ROOT 文件重建事件，重建程序由本次保存的源码快照编译。
每次从头执行创建一个独立的结果目录；参数文件快照不证明它们曾用于历史排序。
导出 `results.json`、`fits.csv`、`histograms.root`、PNG/PDF 图像、源码快照和构建日志。
输入 ROOT 和旧事件文件保持原样；Notebook 检查其 SHA-256 未变。

修改参数后可重跑分析和绘图单元。修改本目录的 Python 或 C++ 实现后，应重启
Jupyter 内核再从头运行，避免使用已经导入或编译的旧实现。
在项目根目录执行 `python3 -m unittest discover -s tests -v` 验证程序行为。
