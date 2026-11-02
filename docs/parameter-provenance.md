# 参数来源与适用范围

核查日期：2026-11-02。这里登记当前六份 `sort/` 参数的实际内容、Git 来源及缺失证据，
不据目录名将它们绑定到某次测量。[机器可读审阅结果](run-metadata.json) 保存每个文件的
SHA-256、完整参数行和当前路径的 Git 历史；`source_run`、`derivation`、`validated_for_runs`
目前均为 `null`，表示尚未查明。

## 当前参数登记

| 目录配置 | 参数文件 | 数值内容 | 已知来源 |
| --- | --- | --- | --- |
| DT5720 PSD | [LaBr3_cali.dat](../DT5720_DPP_PSD/sort/LaBr3_cali.dat) | 通道 0–7 均为 `p0=0, p1=1, p2=0, chi2=0` | 已有工作区；无来源运行或峰拟合记录 |
| DT5720 PSD | [LaBr3_ts_offset.dat](../DT5720_DPP_PSD/sort/LaBr3_ts_offset.dat) | 通道 3 为 `+7634 ps`，其余为 0 | 已有工作区；参考通道和提取方法未知 |
| DT5730 PSD | [LaBr3_cali.dat](../DT5730_DPP_PSD/sort/LaBr3_cali.dat) | 通道 0–7 均为 `p0=0, p1=1, p2=0, chi2=0` | 已有工作区；无来源运行或峰拟合记录 |
| DT5730 PSD | [LaBr3_ts_offset.dat](../DT5730_DPP_PSD/sort/LaBr3_ts_offset.dat) | 通道 0–7 均为 0 | 已有工作区；零值是否代表已对齐未知 |
| DT5730 PHA | [LaBr3_cali.dat](../DT5730_DPP_PHA/sort/LaBr3_cali.dat) | 通道 0–7 均为 `p0=0, p1=1, p2=0, chi2=0` | 已有工作区；无来源运行或峰拟合记录 |
| DT5730 PHA | [LaBr3_ts_offset.dat](../DT5730_DPP_PHA/sort/LaBr3_ts_offset.dat) | 通道 3 为 `+7634 ps`，其余为 0 | 已有工作区；参考通道和提取方法未知 |

当前三份能量文件字节相同，DT5720 PSD 与 DT5730 PHA 的偏移文件也字节相同：

| 文件组 | SHA-256 |
| --- | --- |
| 三份能量参数 | `9553403d04bd0fa87d91b347272c92b6f4f2e10119fa1c7a3309d68872346935` |
| DT5720 PSD / DT5730 PHA 偏移 | `5ac760f7246c92aa52a0954db4caa7cd4c70104914d511b9052badf046a97ee0` |
| DT5730 PSD 偏移 | `0a6de13c947b7a4619a0d68c9a6e00b5f5e8b23cb4b9069e238c3a54296f4887` |

全部通道均有数值行，只能说明程序有八通道的数值输入，不能说明八个通道都经过物理刻度。
当前能量映射保持道址不变；`chi2=0` 没有对应的拟合结果，不能当作拟合质量证据。
排序的时间修正规则是 `timestamp + rounded_offset_ps`，所以 `+7634` 表示通道 3 时间戳增加
7634 ps。这说明代码怎样使用它，不能说明其来源，也不能把 PSD run0009 的现有时间差峰
反过来当作这两份偏移文件的推导记录。

本地另有三份 `alignment/LaBr3_cali.dat`，只列通道 2/3。当前 `Alignment.cpp` 不读取它们；
它们也不满足 `sort` 要求的八通道表，未登记为当前生效的排序参数。

## Git 能确认的范围

六个当前路径在可见历史中都只有一次内容变更：
`b096cd81d7d58c32c059bf6aae67b4cff1884d68`，2026-09-26，
`Fix EOF handling and track analysis configuration`。
该提交说明将已有工作区中的六份文件纳入版本管理，保留数值并补齐末尾换行。
当前文件与该提交、当前 HEAD 的对应内容一致。

这提供了入库时间和内容来源；它不是参数的实验生成时间，也没有给出来源运行、
探测器增益、拟合方法或适用测量条件。可用以下只读命令复查：

```bash
git log --format='%h %ad %s' --date=short -- '*LaBr3_cali.dat' '*LaBr3_ts_offset.dat'
git show --format=fuller --stat b096cd8 -- '*LaBr3_cali.dat' '*LaBr3_ts_offset.dat'
```

## 与现有实验的关系

用户于 2026-11-02 补充：所询问的 DT5730 PSD run0009 和 DT5720 PSD run_4 使用
**⁶⁰Co 放射源，探测器均为 LaBr₃**；其余信息暂时不记得。
这条来源记录保存在[真实样本记录](runs.md)，不自动推广到其他运行。
它可用于后续峰归属的核验，尚不足以确定能量系数、具体探测器编号或时间偏移。

run_4 的 `settings.xml` 明确写入 `energyFormatList=ADCCH`，板级能量系数为 `0, 1, 0`；
同一 XML 与 `run.info` 的单位标签为 `keV`。这些文件共同说明软件保存了该标签和恒等系数，
没有提供以已知峰拟合得到 keV 刻度的证据。当前 BIN 中保存的是 `energy_ch`，不因标签改变单位。
XML 板级时间偏移为 `0.0 NANOS`，也不能用来证明项目 `.dat` 中 `+7634 ps` 的来源。

run0009 的分析从已有排序 ROOT 开始，尚无该历史排序过程的输入和参数归档。
当前目录中的零偏移或恒等能量表不能据此认定为那个 ROOT 实际使用的参数。

后续新建候选参数时，需一同保存输入文件/通道、源峰依据、模型与拟合范围、系数/误差、
时间偏移参考与符号、适用条件及验证结果。候选值写入新文件，通过显式路径运行和归档，
保留这些历史参数以便比较。

## 只读元数据审阅入口

`scripts/audit_metadata.py` 读取六个排序参数路径，以及 `data/<配置>/DAQ/*/` 中现有的
`settings.xml`、`run.info`；不读取 BIN/ROOT 记录，不写回原文件。它保留板级参数与通道覆盖
两个层次，不把描述符默认值、缺失覆盖或单位标签替换成测量结论。
XML 仅提取设备、采集和指定处理参数字段；不复制许可证、网络地址或自由文本。

```bash
python3 -B scripts/audit_metadata.py --output /tmp/labr3-metadata-audit.json
```

输出必须为新文件；也可用 `--repo /path/to/LaBr3` 指定项目。
当前 [run-metadata.json](run-metadata.json) 记录六份参数、五份 XML、六份 `run.info` 的哈希，
并在结束时重新验证这些输入未变。报告保留审阅脚本哈希；`repository_head` 仅表示审阅时的
工作区提交背景，不表示未跟踪的实验配置来自该提交。
`time.real` 保留日志原文，起止时间相减另存为经过时间；不将任一数值解释成测量活时间。
