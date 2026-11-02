# BIN 分卷与通道清单

`scripts/run_pipeline.py --input-manifest inputs.json` 处理显式列出的 BIN 文件。
它与 `--input` 互斥，只用于 `--input-kind bin` 且首阶段为 `raw2root` 的运行。
清单中的 `variant`、`run` 必须与命令行相同；这些是来源声明，不是从 BIN 自动识别的设备信息。

## 格式与顺序

下面声明两个独立通道：通道 2 有两卷，仅第一卷含文件头；通道 3 的文件自带头。
两个流都属于同一次 UNFILTERED 导出：

```json
{
  "schema_version": 1,
  "variant": "DT5730_DPP_PSD",
  "run": 9,
  "export": "UNFILTERED",
  "streams": [
    {
      "id": "ch2",
      "channels": [2],
      "board": 0,
      "header_mode": "first",
      "part_count": 2,
      "files": [
        {"part": 0, "path": "UNFILTERED/Data_CH2_run_9.BIN"},
        {"part": 1, "path": "UNFILTERED/Data_CH2_run_9_1.BIN"}
      ]
    },
    {
      "id": "ch3",
      "channels": [3],
      "board": 0,
      "header_mode": "each",
      "part_count": 1,
      "files": [
        {"part": 0, "path": "UNFILTERED/Data_CH3_run_9.BIN"}
      ]
    }
  ]
}
```

- `schema_version` 固定为整数 `1`，与结果目录中运行清单的 schema version 3 分开编号。
- `export` 为 `RAW`、`FILTERED`、`UNFILTERED` 或 `CUSTOM`。同一清单只能有一类导出；
  路径目录含前三类标签时必须与声明相同。改名的文件无法单靠字节证明导出类别，仍需核实来源。
- 每个流有唯一的非空 `id`，声明允许的 `channels`（0–7）；同一通道不能属于多个流。
  可将混合通道的连续分卷放进同一个流，例如 `channels: [2, 3]`。
  可选的 `board` 限定板号；省略或为 `null` 时允许记录中的任意 Short_t 板号。
- `header_mode: first` 表示仅第 0 卷带两字节头，后续卷从完整记录开始；
  `each` 表示每个文件均有头。全部带头文件的完整 16 位值必须相同，不合并不同字段布局。
- `part_count` 必须为正整数，`files` 恰好列出 `0` 到 `part_count - 1`，卷号不能重复或跳号。
  程序按数字卷号排序（`2` 在 `10` 前），按 `streams` 数组顺序处理各流。
- 文件路径相对**清单所在目录**解析，也可为绝对路径；不支持目录或通配符。
  可选 `sha256` 为该文件的 64 位十六进制校验值，包含它自己的文件头（若有）。
  清单拒绝未知字段和重复 JSON 键。

名称符合 `...run_N[_卷号].BIN` 时，还会核对运行编号和卷号，并拒绝同目录中未列出的同组卷。
任意改名文件依靠清单声明排序；序号连续也不能证明采集没有漏掉末卷或其他通道。

## 校验与输出

在编译或转换前逐条读取文件，检查完整记录边界、通道/板号声明、有限 keV 值和波形容量。
每个文件都必须结束在记录边界，不能将两个截断片段拼成一条记录。
同一通道在流内和跨卷时必须时间非递减；不同通道之间不要求全局有序，后续 `sort` 负责排序。

相同路径、硬链接或非空记录载荷完全相同的文件会被拒绝。载荷比较剔除文件头，因此也能发现
首卷记录被原样复制为无头续卷的情况。相同时间戳和非重复文件之间完全相同的边界记录都会保留；
后者在报告中标记 `boundary_equal_record`，不自动删掉。重复的空载荷允许通过并记录来源。

校验期间原文件保持只读，逐条写入 `inputs/assembled.BIN`，全文件仅保留一个头，记录字节不变。
这个 BIN 留在结果目录供核查，需要约等于所有输入总量的额外磁盘空间；内存不随记录总数增长。
现有 C++ 转换器随后读取它；`n` 对应按流顺序、卷号顺序串接后的全局记录序号。
准备阶段失败会清理未完成 BIN、停止编译和后续阶段，诊断写入 `logs/00-inputs.log`。
编译、输入准备与各处理阶段均受 `--timeout` 限制。

`inputs/` 还保存：

- `source_manifest.json`：原始清单字节副本。
- `resolved_manifest.json`：规范卷号顺序、绝对路径及每个文件实际 SHA-256，供重跑使用。
- `input_report.json`：逐文件字节数/哈希、记录数、全局起止序号（左闭右开）、
  各通道首末时间戳、首末记录与边界信息，以及规范 BIN 和清单的哈希。

运行 `manifest.json` 的 `input_set` 保存以上摘要和归档位置；转换器报告的 `input_records`
必须等于清单逐文件记录数之和。多文件运行要求计数报告，旧版无报告的 `--bin-dir` 程序不能通过。
`scope.declared_parts_complete = true` 表示清单声明的全部卷通过校验；
`scope.acquisition_complete = null` 表示尚不能确认整次采集完整。成功状态只覆盖声明的输入与所选阶段。

## 本地 run_4 示例与重跑

[run4-unfiltered.inputs.json](examples/run4-unfiltered.inputs.json) 列出本地 DT5720 run_4 的
32 卷及其校验值，只选择 UNFILTERED 一类；它不携带原数据文件，换用其他数据时应另建清单。
在项目根目录仅执行转换，不需要能量刻度或时间偏移：

```bash
python3 -B scripts/run_pipeline.py \
  --variant DT5720_DPP_PSD --run 4 \
  --input-manifest docs/examples/run4-unfiltered.inputs.json \
  --stages raw2root --output-dir results/run0004_conversion01
```

从结果重跑的命令与单文件相同；入口使用归档的 `resolved_manifest.json`，不需要原清单 JSON 仍在原处：

```bash
python3 -B scripts/replay_pipeline.py results/run0004_conversion01/manifest.json \
  --output-dir results/run0004_conversion02
```

原 BIN 仍须可读。数据搬迁后，复制归档 `inputs/resolved_manifest.json` 到新文件，
只修改各条目的 `path`，保留每个 SHA-256 和全部流/卷声明；给重跑命令增加
`--input-manifest /path/to/relocated.inputs.json`。入口核对每份内容和声明，拒绝换数据或删掉哈希。
规范 BIN 会重新生成，重跑不会覆盖原结果。更换数据集应发起新的普通运行。
