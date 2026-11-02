这里保存由小型合成数据驱动的回归检查，覆盖三套设备程序。
依赖 Python 3.8 或更新版本、可导入的 PyROOT、`root-config`、GNU Make 和 C++ 编译器。

在项目根目录运行全部检查：

```bash
python3 -m unittest discover -s tests -v
```

也可以单独检查事件构建或原始数据转换：

```bash
python3 tests/test_build.py
python3 tests/test_raw2root.py
python3 tests/test_sort.py
python3 tests/test_alignment.py
python3 tests/test_paths.py
python3 tests/test_analysis.py
python3 tests/test_peak_fit.py
python3 tests/test_make.py
python3 tests/test_bin_inputs.py
python3 tests/test_pipeline.py
python3 tests/test_pipeline_integration.py
```

`fixtures.py` 生成带已知字段值的 BIN 和 ROOT 文件。程序通过已有 Makefile
编译到临时目录，并作为子进程运行；检查结束后删除测试生成的文件。
原始数据转换测试只在临时副本中替换输入路径，不修改项目的 `set.h`。

`test_make.py` 的 8 项检查不依赖 PyROOT 或 ROOT 安装，使用临时源码副本和模拟工具验证
根目录编译入口：并行编译全部 12 个程序、按设备/阶段选择、重复执行不重编、
源码/Makefile/共享头文件触发重编，以及缺失或失效的编译器/ROOT 配置命令、编译失败的传播。
同时覆盖带空格的仓库和工具路径、从其他工作目录执行 `make -C`，以及 12 个阶段原有的
独立编译和 `OBJ`/`out` 输出覆盖。模拟工具检查用于验证编译调度，不能代替真实 C++ 编译和运行。

`test_bin_inputs.py` 的 19 项检查仅使用标准库，覆盖全部 16 种字段组合和两种头部策略，
逐字节核对规范 BIN；覆盖数字卷号（含第 10 卷）、独立通道顺序、重复路径/硬链接/非空载荷、
缺卷、导出类别、头部不一致、截断及跨卷记录、非法数值/波形、通道/板号和时间倒退。
另验证相同时间戳及边界记录保留、空载荷、固定哈希和只改变路径的搬迁清单。

`test_pipeline.py` 的 26 项检查使用临时可执行脚本，不依赖 ROOT，覆盖批处理阶段顺序、
显式参数及副本、输出目录保护、输入/产物哈希、已知分卷拒绝、失败停止、缺失产物、
输入变化、超时及 SIGTERM 中断，以及分析 JSON 的类型与范围检查。
另检查源码/参数/二进制归档的哈希验证、重跑输入迁移、归档路径约束、编译失败与
计数报告/多重数/阶段间守恒，旧程序未报告计数时单文件运行明确保留未知状态，多文件运行失败；
原始分卷在准备完成后发生变化也会令整次运行失败，并将输入未变标志置为假。
`test_pipeline_integration.py` 的 13 项检查通过根目录入口在临时源码副本中编译全部 12 个程序，
并实际验证三种配置的默认源码快照编译及 BIN → 对齐/排序 → 构建 → 分析，
逐字段核对结果、筛选计数、阶段报告和时间谱；另从归档重编并比较重跑计数，验证空输入零计数。
还覆盖从三个 ROOT 阶段起跑、截断 BIN、无效偏移文件和错误阶段的分析输入。
新增的分卷检查覆盖三种配置的完整链、独立通道合并、数据搬迁后的归档重编与分析，
以及准备失败时不启动编译、清单标签/阶段约束、解码计数不符和可选字段/波形/记录序号。
相对路径从项目根目录和外部目录调用，目录及文件名包含空格和中文；原输入和参数哈希不变。

`test_build.py` 的 29 项检查使用显式列出的预期事件分组，逐项比较板号、通道、能量和时间戳，
并检查输出树的名称、标题、分支类型、多重数及记录总数。覆盖：

- 空输入、单条记录、最后一个单重或多重事件。
- 49,999、50,000、50,001 ps 的窗口边界，以及 0、40、80 ns 的首记录窗口示例。
- 相同时间戳、8 重事件、8 重之后的新事件和超出容量的第 9 条记录。
- 窗口内及跨窗口的乱序输入、大时间戳和有符号时间戳的极端跨度。
- 输入文件或树缺失、损坏文件、同名非树对象、分支缺失、类型错误和标量分支含多个叶子。
- 非法通道、非有限能量、无效运行编号及忽略不需要的波形分支。
- 失败时不发布最终文件、保留已有输出、清理临时文件，以及最终文件无法写入时的错误状态。

`test_raw2root.py` 的 19 项检查覆盖三套解码实现：合法空输入、单条/多条记录、
基础与可选字段截断、非法通道、非有限 keV 能量、缺失文件和读取错误。
对无能量字段、仅 keV、短积分、波形及组合字段核对分支类型和原始值；
验证本地文件使用的 `0xCAED` 头部、0/1/10,000 点波形及长度变化，
并拒绝 10,001 点和异常巨大长度。另检查无效运行编号、失败时保留已有输出、
成功替换输出和发布失败后的临时文件清理。

解码器现在只将完整文件头后的记录边界视为正常结束。截断记录会使整次转换失败，
不会发布部分 ROOT 文件；这有意改变了旧版本静默丢弃末尾残缺记录的行为。
keV 分支由错误的无符号整数描述改为 `Double_t`，历史 ROOT 文件不会被自动修复。

`test_sort.py` 的 37 项检查覆盖三套排序实现。使用明确的预期字段检查非零板号、
全范围 `UShort_t` 道址的二次刻度、超出 `2**53` 的时间戳及整数偏移、正负偏移、
半 ps 舍入、整数上下界和溢出拒绝。相同时间戳按通道和原始记录顺序稳定排列。
同时检查无末尾换行、空白行、参数顺序、文件或通道缺失、重复/越界通道、
不完整或多余字段、非有限参数及刻度结果。错误输入、分支类型、波形忽略、
输出保护和排序到事件构建的字段及计数衔接也有覆盖。
另检查错名/越界通道树、其他阶段的树、损坏文件、同名非树对象、仅有元数据的文件，
以及标量被数组或多叶子分支替代的情况，防止把错误输入静默当作空数据。

修复前在 `f3b7a12` 的排序实现上运行四组针对性样本，三套程序共复现 12 次失败：
板号丢失、大道址二次项溢出，以及大时间戳下的 ps 差值和正负偏移丢失。
预期结果来自显式字段、分组和整数边界，不以历史排序文件作为正确性判据。

`test_alignment.py` 的 31 项检查覆盖三套对齐实现：正负时间差、严格窗口边界、
参考命中重复使用、目标命中不重复计入、ps 精度与有符号时间戳的极端跨度，
以及空文件内容、空通道、单通道、默认和显式参考通道、参考及目标通道乱序。
逐项核对时间谱的计数、分箱、均值和输出中的参考通道、窗口、符号及输入计数。
还检查缺失/损坏文件、错误树名与分支类型/形状、通道不符、忽略可选能量及波形、
无效命令行参数和输出保护。

对齐修复前，正窗口边界漏配、参考通道乱序和错误时间戳类型三组样本，在三套旧实现
中共复现 9 次失败。其中参考时间为 1,000,000 ps、目标时间为 0 和 1,000,100 ps 时，
旧版漏掉后一目标；修复后得到一个 −0.1 ns 匹配。判定规则见根目录 README 的时间对齐说明。

四类处理程序都有输出目录设为 `0555` 的检查，验证失败状态、旧输出保留和临时文件清理。
此类检查要求普通用户权限；以 root 运行时会显式跳过，不能据此宣称验证了写入权限失败。

`test_analysis.py` 的 17 项检查验证多重数 1–8 的全部字段、空树、缺失/损坏文件、
错误分支/类型、非法多重数、非法通道、非有限能量、能量门严格边界与拒绝、
通道反序、板号筛选、大时间戳差值、直方图溢出、跨文件 TChain 及调用者文件所有权。
TChain 检查包括后续文件缺失必要分支时的错误处理。
另外分别编译三套原有 `ana.C` 入口，验证它们使用同一套安全读取和筛选逻辑。

`test_peak_fit.py` 使用带背景的已知 Gaussian 合成样本检查峰位、FWHM 和误差，
验证空谱/低统计不报告分辨率、非法配置明确失败、模型积分保持面积，
并读回导出的 JSON 和 ROOT 文件核对计数与配置。拟合及 Notebook 另需 NumPy、Matplotlib；
不依赖 SciPy。

`test_paths.py` 的 15 项检查在临时源码副本中编译三种设备的全部四类程序，验证：

- 从项目根目录和无关工作目录使用绝对路径，从无关目录使用相对路径；输入和输出含空格及中文。
- 显式路径时省略运行编号；保留运行编号并覆盖输入、输出或两者；分别覆盖两份排序参数。
- 完整的合成 BIN → 通道树 → 排序树 → 事件树 → 共享分析，精确核对字段、事件分组和时间谱计数。
- 帮助信息不依赖输入文件，错误/重复/缺值选项明确失败，错误文件路径及缺失输出目录保留输入和旧输出。
- 输出与输入、刻度或偏移文件重合时拒绝，包括相对路径别名、符号链接和硬链接。
- Notebook 从保存的源码快照编译重建事件，快照包含 `common/CommandLine.h`，输入校验值保持不变。

这些完整链检查使用已知字段和参数的合成数据；真实实验 BIN 的处理链仍待实验配置核验后重跑。

2026-09-28 在 ROOT 6.40.04、Python 3.14.4 上运行全部 **45 个检查方法**通过，
包括 22 项构建、4 项 BIN 转换、13 项分析和 6 项拟合/导出检查。
构建、BIN 转换和旧宏兼容性检查分别覆盖三套设备实现。
真实样本的统计和模型限制见 [docs/runs.md](../docs/runs.md)。

2026-10-30 完成 P0-03 时，在 Python 3.13.5、ROOT 6.34.10 上分两组执行当时的 **59 个检查方法**：
22 项构建、18 项 BIN 转换、13 项分析共 53 项核心检查，以及 6 项拟合/导出检查全部通过。
其中多重数统计数组的 PyROOT 读取在修复前失败，限定长度常量的命名空间后通过。
拟合检查使用临时虚拟环境 `/tmp/labr3-validation-20261030`，继承系统的 ROOT 与
NumPy 2.2.4，并安装 Matplotlib 3.11.2；未改动系统 Python 或 Jupyter 内核。
本次日志位于该临时目录的 `core-tests.log` 和 `fit-tests.log`。

在本次临时环境仍存在时，可从项目根目录用以下命令复查全部检查：

```bash
env PATH="/opt/root/6.34.10/bin:$PATH" \
    MPLCONFIGDIR=/tmp/labr3-validation-20261030/matplotlib \
    /tmp/labr3-validation-20261030/bin/python -B -m unittest discover -s tests -v
```

其他环境需替换 ROOT 和 Python 路径，并确保上述依赖可导入；测试本身不依赖这些绝对路径。

同日完成 P0-04/P0-05 后，在上述临时环境运行全部 **91 个检查方法**通过：
32 项排序/参数、22 项构建、18 项解码、13 项分析和 6 项拟合/导出。
本次用 unittest 依次加载 `test_sort.py`、`test_analysis.py`、`test_build.py`、
`test_peak_fit.py`、`test_raw2root.py`，日志保存在
`/tmp/labr3-validation-20261030/sort-full-tests.log`。
上面的完整复查命令会自动包含新增排序检查；单独复查时沿用同一环境变量和解释器，
将 Python 后的参数替换为 `-B tests/test_sort.py -v`。

继续完成 P0-06/P1-06 后，分组覆盖的全部 **139 个检查方法**通过：31 项对齐、
37 项排序、29 项构建、19 项解码、17 项分析和 6 项拟合/导出。
核心组日志为 `/tmp/labr3-validation-20261030/contracts-core-tests.log`（106 项）；
补充多叶子分支检查后，重新运行全部对齐和构建检查及新增排序检查，日志为
`/tmp/labr3-validation-20261030/contracts-scalar-tests.log`（61 项，含重复复查的 28 项构建）。
两组去重覆盖 139 项，均无跳过项；输出目录不可写的检查以 UID 1000 实际执行。
仍使用上述临时环境和完整复查命令；全部小样本和二进制均放在临时目录，未重跑真实实验数据。

2026-11-01 完成 P1-01 后，原有 **139 项**回归与新增 **15 项**路径检查全部通过，
共 **154 个检查方法，无跳过项**。沿用 Python 3.13.5 / ROOT 6.34.10 的上述临时环境，
分别运行 `test_paths.py`，以及 `test_alignment.py`、`test_sort.py`、`test_build.py`、
`test_raw2root.py`、`test_analysis.py`、`test_peak_fit.py`。
日志为 `/tmp/labr3-validation-20261101/paths-tests.log` 和 `regression-tests.log`。
上面的完整复查命令已包含新增检查。全链与 Notebook 快照检查使用合成数据，
没有重跑真实实验 BIN 或更新旧 Notebook 输出。

2026-11-02 的 P1-02 只更新格式、命名与输入范围说明，没有修改处理源码，未重复运行上述
154 项回归。本轮另做本地样本核验：清点 101 个 BIN 的大小和前缀，逐记录审阅 run_4
三个导出目录中的 96 卷，并核对同序文件和整组字节流的 SHA-256。
临时编译提交 `5648f7a` 的 DT5720 解码器，首卷的 3,731 条记录全部保存字段及波形与 BIN
逐项相等，输入哈希不变；续卷单独输入以非零状态失败，没有最终输出或残留临时文件。
这项检查依赖本地数据，未加入可移植的合成回归集；完整实验处理链和物理结论仍待核实。

本次脚本与日志在 `/tmp/labr3-data-format-20261102/`：`audit_run4.py`、`inventory.json`、
`run4-audit.json`、`decoder-check.log`、`decoder-verification.json`。临时文件仍存在时，
可在项目根目录用 `python3 -B /tmp/labr3-data-format-20261102/audit_run4.py` 复查只读分卷审阅；
编译及单卷调用的完整参数记录在 `decoder-check.log`。
重要计数、哈希、配置来源和适用范围已保存在 [真实样本记录](../docs/runs.md)。

同日完成 P1-03 后，执行 `python3 -B tests/test_make.py -v`，新增 **8 项**编译入口检查通过。
在 ROOT 6.34.10 下实际执行以下命令，三种配置的 **12 个程序全部编译成功**：

```bash
make -j4 ROOT_CONFIG=/opt/root/6.34.10/bin/root-config
```

根目录产物保存在 `.build/`。再次从 `/tmp` 通过 `make -C` 调用，全部产物的修改时间不变；
本机 `DT5720_DPP_PSD/raw2root/set.h` 和仓库中两份历史已跟踪二进制的 SHA-256 也保持不变。
复用 `test_paths.py` 的断言，将可执行文件指向集中产物，复查 **4 个检查方法**通过：
12 个程序的帮助入口、三种配置在根目录和外部目录的完整合成处理链、相对路径处理链，
以及 Notebook 源码快照独立编译。链路检查包含解码、对齐、排序、构建及分析的字段和计数核对。
本次没有重复运行其余既有回归，也没有重新处理真实实验数据。

临时日志目录为 `/tmp/labr3-validation-20261102/`：`make-all.log`、`make-incremental.log`、
`unified-pipeline.log`；适配现有检查到集中产物的脚本为 `verify_unified_build.py`。
日志中三份解码程序各有一条已有的 `fclose` 模板参数属性警告，编译退出状态均为零。
临时脚本仍存在、且上述编译已完成时，可复查：

```bash
env PATH="/opt/root/6.34.10/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/labr3-validation-20261030/matplotlib \
    /tmp/labr3-validation-20261030/bin/python -B \
    /tmp/labr3-validation-20261102/verify_unified_build.py
```

同日完成 P1-04 后，新增 **16 项调度检查和 5 项实际程序集成检查全部通过**。
调度组执行 `python3 -B tests/test_pipeline.py -v`；集成组沿用上述 ROOT/Python 环境：

```bash
env PATH="/opt/root/6.34.10/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/labr3-validation-20261030/matplotlib \
    /tmp/labr3-validation-20261030/bin/python -B tests/test_pipeline_integration.py -v
```

集成组先在临时源码副本中并行编译 12 个程序，再通过正式批处理 CLI 调用。
三种配置均从已知 BIN 得到 4 条校准命中、2 个双重事件，指定门选出 1 对，时间谱均值为 0 ns；
同时核对板号、通道、能量、整数 ps 时间戳、对齐计数及原始输入/参数哈希。
其他用例验证从原始通道树、排序树、事件树开始，以及截断输入、坏参数和错误分析输入的停止行为。
集成日志为 `/tmp/labr3-validation-20261102/pipeline-integration.log`。
本轮未重跑其余既有检查，没有重新处理实验 BIN、修改历史 ROOT 或更新旧 Notebook 输出。

继续完成 P1-05 后，在同一 Python 3.13.5 / ROOT 6.34.10 环境分组验证，
**去重覆盖全部 193 个检查方法，无跳过项**：139 项原有功能回归、15 项运行路径、
8 项编译入口、24 项批处理/归档检查和 7 项实际程序集成。
三种配置均验证默认源码快照编译、实际执行文件来自快照、完整阶段计数和零记录输入。
归档重跑重新编译后得到相同的字段、事件分组和分析计数；改变原参数或原二进制不影响已保存副本，
修改归档内容或提供不同输入哈希会被拒绝。Notebook 快照补齐新共享头后也编译运行通过。
增量依赖检查对源码和产物显式设置临时时间戳，覆盖两个共享头，避免依赖真实时钟精度。

本次日志均在 `/tmp/labr3-validation-20261102/`：

- `records-regression.log`：139 项（对齐 31、排序 37、构建 29、解码 19、分析 17、拟合 6）。
- `records-paths-make.log`：运行路径 15 项及编译入口 8 项。
- `records-pipeline-tests.log`：调度、归档、重跑及计数契约 24 项。
- `records-integration.log`：实际程序及归档重编的 7 项集成检查。
- `records-replay-check.log`：加强重跑清单布局校验后，补查其中 1 项重编/分析检查，不重复计入总数。

可使用文首的完整 discovery 命令复查；分组加载时应保留 ROOT 原有的 Python 搜索路径，
在 Python 中用 `sys.path.insert(0, 'tests')` 加入测试目录，不覆盖已有 `PYTHONPATH`。
本轮仅处理合成样本，没有重新处理实验 BIN、历史 ROOT 或旧 Notebook 输出。

P1-07 在同一环境新增并复查 **58 项**：清单字节校验 19 项、批处理 26 项、实际程序集成
13 项，均通过，无跳过项。未重复运行未改动的 C++ 算法及其余分析回归；结合上轮基线，
当前共有 220 个已覆盖检查方法。复查命令：

```bash
python3 -B tests/test_bin_inputs.py -v
python3 -B tests/test_pipeline.py -v
env PATH=/opt/root/6.34.10/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/labr3-validation-20261030/matplotlib \
    /tmp/labr3-validation-20261030/bin/python -B tests/test_pipeline_integration.py -v
```

日志在 `/tmp/labr3-validation-20261102/`：`bin-input-tests.log`、`multifile-pipeline-tests.log`、
`multifile-integration.log`。集成组实际编译全部 12 个程序，另有归档源码构建和搬迁后重跑。
最后对输入变更标志的修正由批处理组复查，未重复整组编译。

本轮也通过正式入口转换真实 run_4 的 UNFILTERED 32 卷，所有产物写入临时新目录：

```bash
env PATH=/opt/root/6.34.10/bin:/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
    /tmp/labr3-validation-20261030/bin/python -B scripts/run_pipeline.py \
    --variant DT5720_DPP_PSD --run 4 \
    --input-manifest docs/examples/run4-unfiltered.inputs.json --stages raw2root \
    --output-dir /tmp/labr3-validation-20261102/run4-unfiltered-manifest \
    --root-config /opt/root/6.34.10/bin/root-config
env PYTHONDONTWRITEBYTECODE=1 /tmp/labr3-validation-20261030/bin/python -B \
    /tmp/labr3-validation-20261102/verify_run4_multifile.py
```

已有结果目录会被拒绝；重新转换须更换目录，并相应调整临时核验脚本的目录。
`run4-multifile-pipeline.log`、`run4-multifile-verification.log/json` 保存结果。
逐条核对 118,048 条保存字段与 15,110,144 个波形采样值，`n`、`dt` 和分支类型均匹配；
规范 BIN 哈希与先前只读分卷审阅一致。核验脚本允许无记录通道不保存树，与解码器既有行为一致。
原始分卷与 XML 哈希未变；本次只做转换，没有施加刻度/偏移或覆盖历史 ROOT。

事件构建必须遵循以下规则：时间戳按 ps 存储；以事件首条记录起算，严格小于
50 ns 的记录进入同一事件；恰好 50 ns 的记录开启下一事件。容量为 8；同窗第 9 条
记录或时间戳倒退会令程序以非零状态退出。只有完整处理且输出写入成功，才将同目录
下的临时文件改名为最终输出；处理失败时保留原有最终文件。

历史 ROOT 文件仅用于观察结果变化。事件正确性由上述规则和合成样本确定，
不能以旧版本遗漏末事件的结果作为期望值。
