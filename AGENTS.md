# LaBr3 项目约定

## 文献检索约定

本节补充文献检索规则；README.md、PLAN.md 和各子目录中原有的项目规则继续保留。

- 文献库路径：`/media/sf_works/papers`。
- 允许使用本项目 `.agents/skills/paper-search/` 中的 `paper-search` 技能，对该共享文献库进行只读检索。
- 文献库及其 `.cache/` 均保持只读；不修改论文、笔记、索引、日志、队列和审核状态，不运行索引重建、入库或其他写入操作。
- 检索结果按用户指定的目标路径保存到当前项目。新提取的文本、PDF 页面图和其他临时文件放在当前项目的 `.cache/paper-search/` 或 `/tmp`，不得写入共享文献库。
- 按研究问题整理主要结论、方法及适用条件、与本项目的关系、已有采用意见和证据缺口，附上可读取的笔记与原文位置。需要核实的重要结论检查原 PDF，并注明从本地 PDF 第 1 页开始计算的页码；未核原文的内容明确标注“据既有精读笔记”。

### 检索脚本的只读调用

共享库的 `knowledge_index.py` 默认搜索可能创建或更新持久缓存。凡在本项目中调用该脚本检索，包括技能正文中的示例命令，都必须加 `--no-cache`，并使用 `python3 -B` 避免生成库内 Python 字节码。库内已有缓存只允许读取。若脚本不支持该选项，改用 `rg` 等只读文本工具，不退回可能写缓存的默认搜索。

```bash
PAPER_LIBRARY_ROOT="/media/sf_works/papers"
python3 -B "$PAPER_LIBRARY_ROOT/.agents/skills/paper-read/scripts/knowledge_index.py" --root "$PAPER_LIBRARY_ROOT" --no-cache search-topic "关键词" --limit 10
python3 -B "$PAPER_LIBRARY_ROOT/.agents/skills/paper-read/scripts/knowledge_index.py" --root "$PAPER_LIBRARY_ROOT" --no-cache search-review "关键词" --context --limit 10
```

### 调用示例

```text
$paper-search

围绕当前项目的【具体研究问题】，检索共享文献库中的相关论文。
整理主要结论、方法条件、与本项目的关系、已有采用意见及证据缺口，
附上笔记和原文位置；需要核实的重要结论请检查原 PDF 并注明页码。
将结果保存到当前项目的【目标文件路径】。
```
