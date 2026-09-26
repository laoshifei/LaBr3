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

## PHA 时间分析宏

在 `DT5730_DPP_PHA/analysis/` 中启动 ROOT 后执行：

```cpp
.L ana.C
ana t;
t.TimeResolution();
```

宏默认读取 `../build/run0014_build.root`，选取 `m == 2` 的事件，
并按通道 2 的位置确定时间差方向。当前读取数组长度为 2，适用于
该类双通道样例；传入其他 ROOT 树前，需确认所有事件的多重数不超过 2，
并按实际通道配置调整筛选条件。

原始实验数据、生成的 ROOT 文件及新增编译产物由 `.gitignore` 排除，
在本地保留，不随源码提交。
