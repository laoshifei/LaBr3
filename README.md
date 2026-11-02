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

`sort` 读取并保留板号、通道、能量和时间戳，只读取所需的标量分支。
二次刻度在浮点数中计算，避免大于 46,340 的道址在平方时发生整数溢出。
时间偏移先舍入到整数 ps（半 ps 远离零），再以整数加法修正时间戳；
超出 `Long64_t` 范围时失败退出。相同修正时间戳按通道号、再按通道内原始记录顺序排列。
空输入生成空排序树；无波形输入同样可处理。排序结果也在成功关闭临时文件后才发布。

`build` 的时间戳单位为 ps，事件窗口以首条记录为基准，恰好达到 50 ns
时开启下一事件。空输入输出空树，最后一个事件会完整写入；同窗多重数超过 8
或输入时间戳倒退、通道越界、能量非有限时，程序报错并以非零状态退出。
只读取所需的四个标量分支，不读取可选波形。成功处理并完成写入后才发布最终
文件；失败运行保留已有输出，成功运行会替换同名输出。

## 编译

需要安装提供 `root-config` 的 ROOT、C++ 编译器和 GNU Make。
保留完整源码目录结构，各阶段会引用 `common/CommandLine.h` 和 `common/StageReport.h`。
在项目根目录执行：

```bash
make -j4                       # 编译三种配置的全部 12 个程序
make DT5730_DPP_PSD             # 只编译一种配置的四个阶段
make DT5730_DPP_PSD/sort        # 只编译一个程序
make check-env                 # 只检查编译器和 ROOT 配置命令
make help
```

根目录入口调用各阶段现有的 Makefile，产物集中放在 `.build/<配置>/<阶段>`，例如
`.build/DT5730_DPP_PSD/sort`；`.build/` 已忽略，不覆盖源码目录中的历史可执行文件。
重复执行会按源码、头文件和阶段 Makefile 的修改时间增量编译，`-j4` 最多同时编译四个程序。
缺少编译器、`root-config` 或配置命令执行失败时，在启动编译前报错；编译或链接失败也返回非零状态。

默认使用 PATH 中的 `g++` 和 `root-config`。可先加载所用 ROOT 安装的 `thisroot.sh`，
也可以显式指定工具；`CXX` 和 `ROOT_CONFIG` 各接受一个可执行文件名或绝对路径：

```bash
make -j4 ROOT_CONFIG=/path/to/root/bin/root-config CXX=g++
make -C /path/to/LaBr3 DT5730_DPP_PSD
```

更换编译器、ROOT 安装或编译选项后，用 `make -B` 强制重编，或在其后指定设备/阶段目标。
各程序目录内原有的 `make` 仍可独立使用，也支持 `ROOT_CONFIG`；其产物仍在该程序目录。
例如在 `DT5730_DPP_PHA/sort/` 执行 `make` 后运行 `./sort 14`。
仅使用运行编号时，输入、输出和参数沿用调用目录下的历史相对路径。
使用集中产物从根目录运行时，推荐显式传入下面的路径选项，无需修改 `raw2root/set.h`。

## 运行时路径

三种设备的四类程序使用同一组路径选项：

```text
raw2root [RUN_NUMBER] [--input FILE] [--output FILE]
alignment [RUN_NUMBER] [--input FILE] [--output FILE] [--reference CHANNEL]
sort [RUN_NUMBER] [--input FILE] [--output FILE] [--calibration FILE] [--offsets FILE]
build [RUN_NUMBER] [--input FILE] [--output FILE]
```

任一程序都支持 `--help` 或 `-h`。同时指定 `--input` 和 `--output` 时可省略运行编号；
带运行编号时可以只覆盖其中一个路径，未指定的路径保留原规则。选项与运行编号的
先后顺序不限。未知选项、重复的路径或参数选项、空值、缺少选项值及多个运行编号会报错。

所有相对路径都相对于**调用时的工作目录**。输入路径是完整文件名；`raw2root --input`
直接读取指定的单个 BIN，不再拼接运行目录或文件前缀。输出父目录需提前创建，
路径含空格时请加引号。输出不能与输入或排序参数文件指向同一个文件，符号链接及硬链接
也会检查；其他已有输出仍按原规则在处理成功后替换。

`sort` 的两份参数文件可分别覆盖。缺省值仍为当前工作目录的 `./LaBr3_cali.dat`
与 `./LaBr3_ts_offset.dat`；从项目根目录或其他位置运行时，应显式传入适用的参数文件。

下面展示从项目根目录运行各阶段的写法。将 BIN 路径和参数文件换为本次测量的实际文件，
先编译相应配置，并按处理顺序执行；上一步失败时停止。

```bash
make DT5730_DPP_PSD
mkdir -p results/run0014
./.build/DT5730_DPP_PSD/raw2root --input "/path/to/Data_run_14.BIN" --output results/run0014/raw.root
./.build/DT5730_DPP_PSD/alignment --input results/run0014/raw.root --output results/run0014/alignment.root --reference 2
./.build/DT5730_DPP_PSD/sort --input results/run0014/raw.root --output results/run0014/sorted.root \
  --calibration DT5730_DPP_PSD/sort/LaBr3_cali.dat --offsets DT5730_DPP_PSD/sort/LaBr3_ts_offset.dat
./.build/DT5730_DPP_PSD/build --input results/run0014/sorted.root --output results/run0014/events.root
```

三种配置各自的事件构建示例（在项目根目录运行，将输入换为对应采集配置的排序文件，
并提前创建 `results/`）：

| 配置 | 命令 |
| --- | --- |
| DT5720 PSD | `./.build/DT5720_DPP_PSD/build --input data/DT5720_DPP_PSD/run0014_sort.root --output results/dt5720_events.root` |
| DT5730 PSD | `./.build/DT5730_DPP_PSD/build --input data/DT5730_DPP_PSD/run0009_sort.root --output results/dt5730_psd_events.root` |
| DT5730 PHA | `./.build/DT5730_DPP_PHA/build --input data/DT5730_DPP_PHA/run0014_sort.root --output results/dt5730_pha_events.root` |

从其他工作目录调用时，对程序、输入、输出和参数文件使用绝对路径即可。
分析读取器已有 `labr3::EventReader("/path/to/events.root")` 文件参数；Notebook 的
输入及结果目录继续由参数单元指定。

## 批处理入口

`scripts/run_pipeline.py` 将选定阶段按顺序运行，使用 Python 3.8+ 标准库调度现有程序。
默认先保存本次使用的源码，再从结果目录中的 `source_snapshot/` 编译所选程序，
实际执行的二进制也保存在该快照的 `.build/` 中；分析脚本及共享分析头同样从快照加载。
包含 C++ 阶段时需要 GNU Make、C++ 编译器和 `root-config`；可通过 `--root-config`、
`--cxx` 指定工具，`--jobs` 指定编译并行数（默认 2）。构建工具的版本、哈希、ROOT 编译/链接选项、
相关环境变量与实际构建命令均写入运行记录，完整编译输出在 `logs/00-compile.log`。
只有选择 `analysis` 时，运行脚本的 Python 才需要可导入的 PyROOT 和 NumPy。

以下命令处理一个 BIN，显式使用本次测量适用的刻度及偏移文件。
输出目录必须尚不存在；脚本会创建目录及其父目录，重复执行需换一个结果目录名：

```bash
python3 -B scripts/run_pipeline.py \
  --variant DT5730_DPP_PSD --run 14 --input "/path/to/Data_run_14.BIN" \
  --calibration "/path/to/calibration.dat" --offsets "/path/to/offsets.dat" \
  --output-dir results/run0014_attempt01
```

`--run` 是 0–2147483647 的记录标签，不用于猜测输入路径、判断设备或确认采集完整性。
设备必须为三种配置之一。命令行中的相对路径按调用时的工作目录解析；输入清单内部的
文件路径按清单所在目录解析。
从其他目录执行时使用脚本的绝对路径即可。`--help` 不要求 ROOT 或数据文件。

`--input-kind` 声明输入所处阶段，缺省为 `bin`。未给 `--stages` 时按下表选择：

| 输入种类 | 输入内容 | 默认阶段 |
| --- | --- | --- |
| `bin` | 一个完整 BIN 文件或显式 BIN 清单 | `raw2root,sort,build` |
| `raw` | 按通道保存的原始 ROOT 树 | `sort,build` |
| `sorted` | 已排序、已修正的标量命中树 | `build` |
| `events` | 已构建的事件数组树 | `analysis` |

可用 `--stages` 显式选择阶段，顺序为 `raw2root,alignment,sort,build,analysis`，
不能重复、倒序或跳过所需输入。例如从排序文件开始只构建事件：

```bash
python3 -B scripts/run_pipeline.py \
  --variant DT5730_DPP_PHA --run 14 --input data/DT5730_DPP_PHA/run0014_sort.root \
  --input-kind sorted --stages build --output-dir results/pha_run0014_rebuild01
```

`alignment` 是可选的检查分支，排序仍读取原始通道树；它不会自动生成或修改偏移参数。
选择 `sort` 必须同时给 `--calibration` 和 `--offsets`；选择 `analysis` 必须给
`--analysis-config`。参数文件先复制到结果目录，再让子进程读取副本。
`--reference` 只用于 `alignment`，省略时保留编号最小非空通道的规则。
不参与所选阶段的参数选项会报错，避免看似指定却没有使用。

若需要全部阶段及能谱、时间谱导出，给上述 BIN 命令增加：

```text
--stages raw2root,alignment,sort,build,analysis --reference 2
--analysis-config analysis/example_settings.json
```

这两行表示附加参数，需接到同一条命令上。[分析配置示例](analysis/example_settings.json)
使用通道 2/3、不限制板号、不施加能量门；应按测量配置调整，格式见
[分析说明](analysis/README.md)。分析输出计数与直方图，本入口不执行峰拟合。

成功时生成所选阶段的 `raw.root`、`alignment.root`、`sorted.root`、`events.root`，
以及可选的 `analysis/results.json`、`histograms.root` 和只含表头的 `fits.csv`。
`logs/` 保存各子进程的标准输出与错误；`parameters/` 保存实际读取的参数副本；
`manifest.json` 保存输入/输出/二进制 SHA-256、命令参数、耗时、退出码、Git 工作区状态和阶段状态。
其中 `source_snapshot` 列出保存的源码文件及各自哈希，`software.build` 记录从它们编译的过程。
只有全部所选阶段成功、计数校验通过且输入、实际执行文件、源码和参数副本哈希未变，
整体状态才为 `succeeded`。

各 C++ 程序在成功发布输出后打印一条 `LABR3_STATS` JSON 记录，批处理将其保存到阶段的
`counts` 字段；分析计数来自 `analysis/results.json`。当前计数字段如下：

| 阶段 | 计数字段 |
| --- | --- |
| `raw2root` | `input_records`、`output_hits` |
| `alignment` | `input_hits`、`matched_pairs` |
| `sort` | `input_hits`、`output_hits` |
| `build` | `input_hits`、`output_hits`、`output_events`、`events_m1`–`events_m8` |
| `analysis` | `input_events`、`input_hits`、`channel_pairs`、`selected_pairs` |

转换和排序应保留命中数；事件构建的各多重数计数之和应等于事件数，按多重数加权后应等于
输入命中数。相邻阶段的命中/事件计数也需一致，任何不一致都会使运行失败。
对齐是辅助匹配，不要求匹配数等于输入命中数；分析能量门允许减少入选对数。
`counts_complete` 表示全部所选阶段都提供了计数。

任一步失败即停止，后续阶段记为 `not_run`；已完成阶段和失败日志保留供核查，
整体标为 `failed` 并返回非零退出码。`--timeout SECONDS` 可限定输入准备、编译及每阶段耗时，默认不限时；
超时、Ctrl-C 或 SIGTERM 会停止当前子进程组。强制杀死进程或断电可能留下 `running`，
应视为未完成，不能按输出文件存在就认定成功。

`--input` 保留单文件接口；识别到 CAEN 数字续卷或同目录同名前缀的数字分卷时，
要求改用 `--input-manifest`。JSON 清单显式声明导出类别、各流的通道、卷号和头部策略，
支持仅首卷带头的续卷及每文件带头的独立通道输入。按数字卷号检查缺卷、重复文件、
截断记录及通道内时间倒退，不以相同时间戳去重。格式、限制和重跑方法见
[BIN 输入清单](docs/input-manifests.md)。本地 32 卷示例可直接只做转换：

```bash
python3 -B scripts/run_pipeline.py \
  --variant DT5720_DPP_PSD --run 4 \
  --input-manifest docs/examples/run4-unfiltered.inputs.json \
  --stages raw2root --output-dir results/run0004_conversion01
```

该模式在 `inputs/` 保存原清单、逐文件计数与哈希、供重跑的已解析清单，以及记录字节不变的
统一 BIN；后者约需原 BIN 总量的额外磁盘空间。原文件保持只读。
`scope.declared_parts_complete = true` 表示声明的卷均通过校验；孤立或改名文件无法靠
文件名证明完整，所有运行的 `scope.acquisition_complete` 仍为 `null`。
`succeeded` 仅表示对指定输入完成所选阶段。

如需复用现有程序，可以先运行根目录 `make`，然后显式加 `--bin-dir .build`。
脚本会将所选程序复制到结果目录的 `executables/` 再运行，标记 `software.build_origin = prebuilt`。
此模式中的源码快照仅描述当时的工作区，不能证明它与外部二进制对应；无计数报告的旧程序
在单文件运行中保留 `counts = null`、`counts_complete = false`。
默认快照编译和多文件清单模式均要求完整计数报告。

成功的 schema version 2/3 运行可以直接按归档重跑，当前运行保存 version 3；
必须使用原结果目录之外的新目录：

```bash
python3 -B scripts/replay_pipeline.py results/run0014_attempt01/manifest.json \
  --output-dir results/run0014_replay01
```

重跑先校验保存的源码、参数及原输入哈希，再调用归档中的驱动脚本：默认模式从归档源码重新编译，
预编译模式使用保存的二进制副本。不会读取后来修改的项目源码、原参数或原二进制。
原始输入仍须可读。若移动了单文件输入，用 `--input /new/path/input.BIN` 指定，内容哈希仍须相同。
多文件运行使用归档的已解析清单；搬迁时通过 `--input-manifest` 提供只修改路径的新副本，
保留全部声明和逐文件校验值。统一 BIN 会重新生成。
旧格式或未成功完成的运行不能通过此入口重跑。
源码编译模式可显式覆盖 `--root-config`、`--cxx`、`--jobs`；两种模式均可调整 `--timeout`，
仍需安装兼容的外部依赖。
新记录保存实际环境及 `replayed_from` 原清单校验值。比较重跑结果应按字段和统计量，
不要求 ROOT 文件或重新编译的二进制字节完全相同。

## BIN 格式、命名与输入范围

以下是当前三套 `raw2root/main.cpp` 实际实现的读取约定，由合成样本和本地文件核对；
它不表示程序支持所有 CAEN 二进制导出格式。三套解码布局相同，程序不会根据目录名、
文件名或文件头高位验证采集型号与固件模式，输出树标题取决于选择的程序。

文件以 **2 字节小端无符号整数**开头，后面逐条存储记录，字段之间没有填充字节。
文件头的低四位控制整个文件的可选字段，高位保留原处理方式，不作含义或标识校验：

| 掩码 | 置位时存在的内容 |
| --- | --- |
| `0x0001` | 原始道址 `energy_ch` |
| `0x0002` | 浮点能量 `energy_keV` |
| `0x0004` | 短积分 `energy_short` |
| `0x0008` | 波形编码、点数及波形样本 |

例如本地文件头字节 `ED CA` 按小端得到 `0xCAED`，低四位为 `0xD`，
包含原始道址、短积分和波形，没有 `energy_keV`。当前程序也接受高位为零的合成头，
不能仅凭扩展名或头部高位就认定一个任意文件符合此布局。

每条记录按下表顺序读取；省略未启用的字段后，紧接下一字段：

| 字段 | 字节数与类型 | 出现条件及 ROOT 保存方式 |
| --- | --- | --- |
| `board` | 2，有符号整数 | 必须；`board/S` |
| `channel` | 2，有符号整数 | 必须，范围 0–7；`channel/S` |
| `timestamp` | 8，有符号整数 | 必须，项目按 ps 解释；`timestamp/L` |
| `energy_ch` | 2，无符号整数 | `0x1`；`energy_ch/s` |
| `energy_keV` | 8，IEEE binary64 | `0x2`，要求有限值；`energy_keV/D` |
| `energy_short` | 2，无符号整数 | `0x4`；`energy_short/s` |
| `flags` | 4，无符号整数 | 必须；当前读取后丢弃，不据此筛选记录 |
| 波形编码 | 1 | `0x8`；当前不解释、不保存 |
| `size` | 4，无符号整数 | `0x8`，允许 0–10,000；`size/i` |
| `data` | `2 × size`，有符号 16 位样本 | `0x8`；`data[size]/S` |

令 `b0`–`b3` 表示相应位是否置位（0 或 1），单条记录长度为
`16 + 2*b0 + 8*b1 + 2*b2 + b3*(5 + 2*size)` 字节，不含文件头。
输出还生成从 0 开始的文件内记录序号 `n/L`；有波形时生成 `dt[size]/S`，
其值是样本序号 `0..size-1`，不是经过采样周期换算的物理时间。
通道树按记录里的 `channel` 分组，板号保留在分支中，文件名中的通道号不参与分组。

仅给运行编号 `N` 时，BIN 路径为
`FILE_PATH/run_N/FILE_HEADN.BIN`（`FILE_HEAD` 与十进制 `N` 直接拼接）。
版本库中的默认值如下；本机 `set.h` 的覆盖值可能不同：

| 程序 | `FILE_PATH` | `FILE_HEAD` |
| --- | --- | --- |
| DT5720 PSD | `../../../data/DT5720_DPP_PSD/DAQ` | `UNFILTERED/Data_CH2@DT5730_1258_run_` |
| DT5730 PSD | `../../../data/DT5730_DPP_PSD/DAQ` | `UNFILTERED/Data_run_` |
| DT5730 PHA | `../../../data/DT5730_DPP_PHA/DAQ` | `UNFILTERED/Data_run_` |

这些是保留的历史默认值，不能据其前缀确定真实设备。相对各 `raw2root/` 目录运行时，
上述 `FILE_PATH` 指向项目上一级的 `data/`；本地已发现的样本位于项目内 `data/`。
推荐使用 `--input` 指定实际文件。BIN 路径里的运行编号不补零，默认 ROOT 输出按
`run%04d.root` 格式至少补足四位；显式指定输入时不从文件名推导运行编号或输出路径。

| 输入组织方式 | 当前支持范围 |
| --- | --- |
| 一个带完整文件头的文件，记录可含多个通道 | 支持；一次调用读取这一个文件 |
| 按通道导出的 `Data_CHx@设备_run_N.BIN` | 可单独转换，也可用清单的独立通道流统一转换；不自动查找其他通道 |
| `RAW`、`FILTERED`、`UNFILTERED` 目录或 `DataR_`、`DataF_` 前缀 | 名称不决定解码方式，也不代表可相加的数据集合；需核对字节布局及来源 |
| 首卷加 `_1`、`_2` 等续卷 | 使用批处理 `--input-manifest`，显式选择仅首卷带头或每文件带头 |
| 多个输入路径、目录或通配符 | 多个文件逐项列入 JSON 清单；不将目录或通配符作为输入 |

直接调用 C++ `raw2root` 仍只读取一个带头文件；不同通道分别调用时需使用不同输出名。
批处理清单先校验并生成单一 BIN，再交给原转换器，所得通道树可直接供 `sort` 使用。
文件结束在完整记录边界不能证明它是运行的最后一卷；首卷成功也可能只覆盖运行的一部分。

本地 DT5720 run_4 的每类导出均有 32 卷，只有首卷含 `0xCAED` 文件头，续卷直接从
记录开始；其 `RAW`、`FILTERED`、`UNFILTERED` 对应卷的 SHA-256 全部相同。
详细证据见 [真实样本记录](docs/runs.md#dt5720_dpp_psd--run_4-分卷核对)。
输入清单会检查缺卷、重复卷号、重复文件内容、记录边界和时间顺序。序号连续或时间相同本身
不足以证明采集完整或判断重复事件，不能直接按时间戳去重，也不能混加三类导出。

## ROOT 输入约定

所有时间戳均为整数 ps，通道范围为 0–7。各阶段只读取所需分支，并检查名称、
数据类型和形状。`alignment`、`sort`、`build` 的标量分支须只含一个标量叶子，
不能用数组或多字段分支替代。

| 读取阶段 | 必要树与分支 | 空输入 |
| --- | --- | --- |
| `alignment` | `tr_ch00`–`tr_ch07`；`channel: Short_t`、`timestamp: Long64_t` 标量 | 默认参考通道记为 −1，无通道对时间谱 |
| `sort` | `tr_ch00`–`tr_ch07`；`board: Short_t`、`channel: Short_t`、`energy_ch: UShort_t`、`timestamp: Long64_t` 标量 | 生成包含完整分支的空 `tr` |
| `build` | `tr`；`board: Short_t`、`channel: Short_t`、`energy: Double_t`、`timestamp: Long64_t` 标量 | 生成包含完整事件分支的空 `tr` |
| 共享分析读取器 | `tr`；`m: UShort_t` 标量及由 `m` 计数的 `board: Short_t`、`channel: Short_t`、`energy: Double_t`、`timestamp: Long64_t` 数组 | 正常返回零计数 |

通道树内的 `channel` 值必须与树名一致。`alignment` 和 `sort` 允许部分通道缺失，
接受解码器产生的无对象空 ROOT 文件；含对象却没有通道树、存在越界或错名通道树、
把其他处理阶段的树作为输入，均会报错。存在合法通道树时可以附带非树元数据。
`build` 和共享分析即使读取空数据，也要求各自的树及必要分支存在。
只有 `energy_keV` 或没有能量分支的原始 ROOT 文件可用于时间对齐，但不满足
`sort` 对 `energy_ch` 的输入要求；排序不会自动在不同能量字段间切换。

文件缺失或损坏、必要分支缺失或不兼容时会给出诊断；命令行程序返回非零状态，
共享分析读取器抛出异常。四类生成 ROOT 文件的程序都先写同目录临时文件，完整关闭
后才发布；输出目录不可写或发布失败时保留已有文件并清理临时产物。

## 时间对齐

在相应 `alignment/` 目录运行 `./alignment 14`，默认使用编号最小的非空通道作参考。
也可执行 `./alignment 14 --reference 3` 显式指定通道；显式参考通道缺失或为空时失败。
所有存在的通道都要求时间戳非递减，遇到乱序会报告通道树和记录位置。

时间差为 **`t_reference - t_channel`，单位 ns**。匹配条件是整数 ps 差值的绝对值
严格小于 1,000,000 ps（1 μs），恰好在正负边界上的记录不匹配。
逐个目标命中寻找最早仍符合窗口的参考命中；每个目标命中最多计入一次，
同一参考命中可以重复使用。这一规则不保证最近邻匹配，时间差谱也不是全部组合。
它与事件构建的 50 ns 首记录窗口不同，符号也与默认分析的 `t_ch3 - t_ch2` 不同。

输出保留历史拼写 `runNNNN_aligiment.root` 和 `h_chRR_chTT` 直方图名称，
每张时间谱使用 20,000 个分箱、范围 −1000 至 1000 ns。
文件同时记录 `alignment_reference_channel`、`alignment_window_ps`、
`alignment_time_difference`、`alignment_matching_rule`、`alignment_reference_selection`，
以及通道存在位掩码 `alignment_present_channels` 和逐通道输入计数 `h_input_entries`。
没有非空通道时参考编号为 −1；仅有一个通道时正常输出元数据，无通道对时间谱。

2026-10-30 修正了正窗口边界会错误跳过参考命中的问题，并避免空通道成为默认参考。
这些修复可能改变历史时间谱计数；已有实验 ROOT 文件未重新生成。

## 刻度和时间偏移参数

每套程序的 `sort/` 目录包含两份参数文件，通道编号为 0–7：

- `LaBr3_cali.dat`：每行依次为 `channel p0 p1 p2 chi2`。
  能量按 `E = p0 + p1 * energy_ch + p2 * energy_ch^2` 计算；
  `chi2` 被读取，但不参与计算。
- `LaBr3_ts_offset.dat`：每行依次为 `channel offset_ps`。
  偏移按上述规则先舍入为整数 ps，时间戳按 `timestamp + rounded_offset_ps` 修正后参与排序。

两份文件都要求通道 0–7 各出现一次，顺序不限。允许空白行及文件末尾没有换行，
不支持注释或额外字段。缺失文件、缺通道、重复或越界通道、不完整记录、非有限数值，
以及超出范围的时间偏移均会使程序报错退出；诊断包含参数文件和相应行号或通道。

本次纳入版本管理的参数来自已有工作区，原文件没有注明对应的运行编号：

| 程序 | 能量刻度 | 时间偏移 |
| --- | --- | --- |
| `DT5720_DPP_PSD` | 所有通道 `p0=0, p1=1, p2=0` | 通道 3 加 7634 ps，其余为 0 |
| `DT5730_DPP_PHA` | 所有通道 `p0=0, p1=1, p2=0` | 通道 3 加 7634 ps，其余为 0 |
| `DT5730_DPP_PSD` | 所有通道 `p0=0, p1=1, p2=0` | 所有通道为 0 |

这些能量系数保留原始道址数值，不能据此认定已经完成 keV 刻度。
分析其他运行数据前，应确认能量系数和时间偏移适用于该次测量。
逐文件 SHA-256、Git 来源、当前适用范围和只读元数据审阅命令见
[参数来源登记](docs/parameter-provenance.md)；实验条件、日志起止时间与用户补充的
⁶⁰Co / LaBr₃ 信息见[真实样本记录](docs/runs.md)。

run0009 原始通道树另有[两点能量刻度候选及复现命令](docs/calibration-run0009.md)，
由 `analysis/energy_calibration.py` 保存峰拟合、系数协方差、区间敏感性和输入/源码归档。
当前仅涉及该输入的 board 0、通道 2/3；低能峰模型及独立线性验证仍待完善，候选未应用到排序参数。

## 能谱与时间差 Notebook

打开 [02_real_run_energy_and_timing.ipynb](jupyter/02_real_run_energy_and_timing.ipynb)，
选择 `LaBr3 (PyROOT)` 内核，从头运行即可。默认使用本地 PSD run0009 的排序文件，
重新构建事件，输出各通道能谱、施加能量门前后的时间差谱、局部峰拟合及残差。
通道对、板号、能量门、分箱和拟合区间均可在参数单元修改。

每次从头运行创建独立的 `results/` 子目录，保存重建 ROOT、图像、拟合表、
JSON 参数与计数、输入 SHA-256 和源码快照。原排序文件和旧事件文件保留。
能量使用存储单位，尚未确认 keV 刻度；时间宽度属于两通道时间差分布。
拟合结果附统计误差、质量指标及区间敏感性检查，不能仅凭收敛认定物理模型正确。
这一流程从已有排序结果开始，历史 BIN 解码、排序过程和刻度来源仍有待核验。

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
