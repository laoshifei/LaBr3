# 共享事件分析

`EventAnalysis.h` 提供三套 ROOT 宏和 Notebook 共用的读取、筛选及直方图逻辑。
`notebook_analysis.py` 负责运行快照、事件重建、拟合、Matplotlib 绘图和结果导出。
入口示例是 [真实数据 Notebook](../jupyter/02_real_run_energy_and_timing.ipynb)。

## 批处理分析

`run_analysis.py` 为根目录批处理入口提供事件计数、能谱和时间谱导出，也可独立执行：

```bash
python3 -B analysis/run_analysis.py --input /path/to/events.root \
  --config analysis/example_settings.json --output-dir results/analysis_attempt01
```

需要 PyROOT 和 NumPy；不需要 Matplotlib。输出目录必须尚不存在。
配置为 JSON 对象，允许的字段及示例见 [example_settings.json](example_settings.json)：
`channel_a/b`、`board_a/b`、`energy_bins/low/high`、`time_bins/low/high`、`gates_a/b`。
门条件写为 `[[low, high], ...]`，例如 `"gates_a": [[400, 700], [1000, 1200]]`；
能量数值沿用输入树的存储单位，时间谱范围单位为 ns。
未知或重复字段、错误类型、非有限数值和非法门范围会被拒绝。
省略字段使用共享 C++ 分析配置的默认值；`results.json` 保存实际生效的全部字段。

输出包括 `results.json`、`histograms.root` 和只含表头的 `fits.csv`，其中 `fits` 为空。
这项任务不执行局部峰拟合、绘图或推断探测器分辨率；这些功能继续由 Notebook 提供。
在全链中，`scripts/run_pipeline.py` 负责隔离运行目录、保存参数副本、记录退出状态并在失败时停止。
分析入口、`notebook_analysis.py` 和 `EventAnalysis.h` 一并保存到源码快照，分析子进程直接加载
这些副本；事件/命中数与上游构建阶段交叉核对。可用 `scripts/replay_pipeline.py` 从成功运行
的归档重跑，并保留原输入和参数校验值。
批处理完整用法见 [项目说明](../README.md#批处理入口)。

## 原始道址谱与两点刻度候选

`energy_calibration.py` 从原始 `tr_chNN` 树读取道址，按指定板号/通道拟合两条参考峰，
保存线性映射候选及区间敏感性。实际 run0009 的结果和限制见
[⁶⁰Co 刻度候选记录](../docs/calibration-run0009.md)。需要 PyROOT、NumPy 和 Matplotlib。

```bash
python3 -B analysis/energy_calibration.py \
  --input DT5730_DPP_PSD/raw2root/run0009.root \
  --config analysis/co60_run0009.json \
  --reference-pdf /path/to/Co-60_tables.pdf \
  --output-dir results/run0009_co60_candidate_attempt01
```

配置示例 [co60_run0009.json](co60_run0009.json) 使用 schema version 1：

- `variant`、`run` 为来源标签，`source_identification` 说明放射源归属依据；
  可选 `input_sha256` 将分析固定到实际输入内容，示例已固定 run0009。
- `reference` 必须包含核素、HTTPS 来源、PDF SHA-256、从 1 起算的 PDF 页码、
  两个递增的 `energy_keV` 和对应 `standard_uncertainty_keV`。
- `channels` 中每个板号/通道组合必须唯一；`ranges` 是两条峰的 nominal 边界，
  `range_checks` 保存其他命名方案。每对范围须有序且不相交，每个至少含 8 个 bin，
  边界必须为半整数。完整字段名见示例；错误类型、未知或重复字段会被拒绝。

程序读取每棵同名通道树的最高 cycle，记录其他 cycle 而不累加，要求标量
`board: Short_t`、`channel: Short_t`、`energy_ch: UShort_t`。记录通道必须与树名一致；
板号筛选后的全部记录填入 65,536 个 bin，整数道址位于中心，计数与树中选中记录交叉核对。
不读取波形，不依赖排序文件、符合事件或历史能量参数。

结果必须保存到新目录。`manifest.json` 保存输入/产物哈希、配置/源码副本、软件版本和状态；
`results.json` 保存每个区间的完整峰拟合、系数协方差、条件宽度及区间跨度；另有 `fits.csv`、
`spectra.root` 和 nominal 拟合/残差 PNG。可选 `--reference-pdf` 必须与配置哈希一致，
提供时会复制到归档；省略时仅保留参考出处声明，程序不会下载或核验参考原文。
输入、配置、源码及所提供参考副本均作哈希复查。

该入口使用下文的共享峰模型。两点确定两个系数，刻度自由度为 0，所有系数均标记为候选，
不据此声明线性已验证；程序不生成 `.dat`。误差传播保留峰位/宽度相关性，两个峰拟合及
两个参考能量按独立处理，模型/范围系统不确定度不在误差内。任一峰数值不可用时，
该方案的 `candidate` 为 `null`，保存原因；分析成功状态不等于得到可采用的物理刻度。
输入或配置错误返回非零；新目录创建后的异常和键盘中断在清单中记录失败或中断状态。

归档源码位于 `source_snapshot/analysis/`，包含该入口和 `notebook_analysis.py`；
用其中的入口、`configuration.json` 和 `reference.pdf` 指向新输出目录即可重跑，
完整命令见结果记录。该清单独立于 `scripts/replay_pipeline.py` 的 pipeline 清单。

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
快照包括共享命令行头文件 `common/CommandLine.h` 和计数报告头 `common/StageReport.h`；重建使用显式 `--input`、`--output`
路径，不要求在临时目录中复刻原有运行编号目录布局。
每次从头执行创建一个独立的结果目录；参数文件快照不证明它们曾用于历史排序。
导出 `results.json`、`fits.csv`、`histograms.root`、PNG/PDF 图像、源码快照和构建日志。
输入 ROOT 和旧事件文件保持原样；Notebook 检查其 SHA-256 未变。

修改参数后可重跑分析和绘图单元。修改本目录的 Python 或 C++ 实现后，应重启
Jupyter 内核再从头运行，避免使用已经导入或编译的旧实现。
在项目根目录执行 `python3 -m unittest discover -s tests -v` 验证程序行为。
