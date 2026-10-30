# LaBr3

基于 C++ 和 ROOT 的 LaBr3 实验数据离线处理程序，包含
`DT5720_DPP_PSD`、`DT5730_DPP_PSD` 和 `DT5730_DPP_PHA` 三套实现。

## 处理流程

各套程序使用相同的目录结构：

| 目录 | 功能 | 输出 |
| --- | --- | --- |
| `raw2root/` | 将 BIN 数据按通道转换为 ROOT 树 | `runNNNN.root` |
| `alignment/` | 绘制参考通道与其他通道的时间差分布 | `runNNNN_aligiment.root` |
| `sort/` | 应用能量刻度、时间偏移，并按时间排序 | `runNNNN_sort.root` |
| `build/` | 按事件首个信号开始的 50 ns 窗口构建符合事件 | `runNNNN_build.root` |
| `analysis/` | 使用 ROOT 宏分析事件时间差 | 直方图 |

`alignment` 用于辅助检查时间偏移，不会自动修改 `sort` 的参数文件。

`raw2root` 逐字段检查 BIN 读取结果。仅含完整两字节文件头的输入是合法空数据；
空文件、截断的头部或记录、读取错误、非法通道、超过 10,000 点的波形和非有限
keV 能量均报错并返回非零状态。通道范围为 0–7；`energy_keV` 保存为
`Double_t`。可选字段仍由文件头低四位控制，高位标记沿用原有处理方式。
解码要求小端平台，保留现有单文件命名规则；板号按原 `Short_t` 字段保存。
输出先写临时文件，完整关闭后才替换最终文件；失败时清理临时文件并保留已有输出。

`build` 的时间戳单位为 ps，事件窗口以首条记录为基准，恰好达到 50 ns
时开启下一事件。空输入输出空树，最后一个事件会完整写入；同窗多重数超过 8
或输入时间戳倒退时，程序报错并以非零状态退出。成功处理并完成写入后才发布最终
文件；失败运行保留已有输出，成功运行会替换同名输出。

需要安装提供 `root-config` 的 ROOT、C++ 编译器和 GNU Make。
在各程序目录内执行 `make`，再以运行编号作为参数执行程序，例如在
`DT5730_DPP_PHA/sort/` 中执行 `./sort 14`。
输入、输出和参数文件使用相对路径，因此应从对应程序目录运行。
首次转换前需根据本地数据位置及文件命名调整 `raw2root/set.h`。

## 刻度和时间偏移参数

每套程序的 `sort/` 目录包含两份参数文件，通道编号为 0–7：

- `LaBr3_cali.dat`：每行依次为 `channel p0 p1 p2 chi2`。
  能量按 `E = p0 + p1 * energy_ch + p2 * energy_ch^2` 计算；
  `chi2` 被读取，但不参与计算。
- `LaBr3_ts_offset.dat`：每行依次为 `channel offset_ps`。
  时间戳按 `timestamp + offset_ps` 修正后参与排序。

文件仅支持数值行，不要添加注释，并保留文件末尾换行；当前读取代码
使用 `stream.good()` 判断记录是否完整，无末尾换行时可能漏读最后一行。

本次纳入版本管理的参数来自已有工作区，原文件没有注明对应的运行编号：

| 程序 | 能量刻度 | 时间偏移 |
| --- | --- | --- |
| `DT5720_DPP_PSD` | 所有通道 `p0=0, p1=1, p2=0` | 通道 3 加 7634 ps，其余为 0 |
| `DT5730_DPP_PHA` | 所有通道 `p0=0, p1=1, p2=0` | 通道 3 加 7634 ps，其余为 0 |
| `DT5730_DPP_PSD` | 所有通道 `p0=0, p1=1, p2=0` | 所有通道为 0 |

这些能量系数保留原始道址数值，不能据此认定已经完成 keV 刻度。
分析其他运行数据前，应确认能量系数和时间偏移适用于该次测量。

## 能谱与时间差 Notebook

打开 [02_real_run_energy_and_timing.ipynb](jupyter/02_real_run_energy_and_timing.ipynb)，
选择 `LaBr3 (PyROOT)` 内核，从头运行即可。默认使用本地 PSD run0009 的排序文件，
重新构建事件，输出各通道能谱、施加能量门前后的时间差谱、局部峰拟合及残差。
通道对、板号、能量门、分箱和拟合区间均可在参数单元修改。

每次从头运行创建独立的 `results/` 子目录，保存重建 ROOT、图像、拟合表、
JSON 参数与计数、输入 SHA-256 和源码快照。原排序文件和旧事件文件保留。
能量使用存储单位，尚未确认 keV 刻度；时间宽度属于两通道时间差分布。
拟合结果附统计误差、质量指标及区间敏感性检查，不能仅凭收敛认定物理模型正确。
这一流程从已有排序结果开始，上游 BIN 解码、排序和刻度来源仍有待验证。

运行说明见 [jupyter/README.md](jupyter/README.md)，
样本与实测计数见 [docs/runs.md](docs/runs.md)，
分析接口和结果说明见 [analysis/README.md](analysis/README.md)。

## ROOT 时间分析宏

在 `DT5730_DPP_PHA/analysis/` 中启动 ROOT 后执行：

```cpp
.L ana.C
ana t;
t.TimeResolution();
```

三套宏共用安全的可变长度读取器，可读取多重数 1–8 的事件；缺少分支、类型错误、
非法多重数或非有限能量会报错。时间谱仅选 `m == 2` 且包含指定通道对的事件，
默认方向为 `t_ch3 - t_ch2`，未通过能量门的事件不填图。
PHA 与 DT5720 默认读取 `run0014_build.root`，不施加能量门；
DT5730 PSD 默认读取 `run0009_build.root`，保留原有两组能量窗口。

可传入配置覆盖默认参数，例如：

```cpp
labr3::AnalysisConfig config;
config.channel_a = 2;
config.channel_b = 3;
config.gates_a = {{592., 611.}, {674., 693.}};
config.gates_b = {{1105., 1137.}, {1253., 1290.}};
t.TimeResolution(config);
```

原始实验数据、生成的 ROOT 文件及新增编译产物由 `.gitignore` 排除，
在本地保留，不随源码提交。

开发回归检查可在项目根目录执行 `python3 -m unittest discover -s tests -v`。
需要 Python、PyROOT、NumPy、Matplotlib、ROOT 编译环境和 Make，
并将对应 ROOT 安装目录的 `bin` 加入 `PATH`。样本与编译产物均生成在临时目录；
覆盖范围见 [tests/README.md](tests/README.md)，后续任务见 [PLAN.md](PLAN.md)。

修复过程与可执行示例见 [Jupyter Notebook](jupyter/01_event_building_and_regression.ipynb)，
内含旧版对比、窗口边界示例和完整回归结果；运行环境说明见 [jupyter/README.md](jupyter/README.md)。
