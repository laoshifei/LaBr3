这里保存项目开发和验证过程的 Jupyter Notebook。

下述启动记录来自 2026-09-28 的运行环境：系统 Python 为 `/usr/bin/python3`，
ROOT 位于 `/opt/root/6.40.04`，内核注册在
`/usr/local/share/jupyter/kernels/labr3-root/`，显示名为 `LaBr3 (PyROOT)`。
迁移环境后需重新检查依赖和内核，不能直接假定这些路径仍可用。

2026-10-30 检查时，当前环境为 Python 3.13.5、ROOT 6.34.10（`/opt/root/6.34.10`），
原 `labr3-root` 内核未注册，系统 Python 缺少 Matplotlib 和 nbclient。
本次回归使用临时虚拟环境补齐绘图依赖；这不等于已配置好 Notebook 内核。
共享分析已修复在 ROOT 6.34 下读取多重数统计数组的兼容问题。

可在终端检查依赖：

```bash
python3 -c 'import ROOT, numpy, matplotlib; print(ROOT.gROOT.GetVersion())'
jupyter kernelspec list
```

在普通用户终端使用本机的通用启动脚本 `~/start_jupyter.sh`：

```bash
~/start_jupyter.sh
```

默认打开 `/media/sf_works` 的文件列表，可以分别进入 `LaBr3/jupyter` 和
`csi_pid_ml/jupyter`，在不同浏览器标签页中同时打开、运行 Notebook。
每个 Notebook 可自行选择内核；本项目选择 `LaBr3 (PyROOT)`。

第一个参数可以指定其他目录，例如只查看本项目：

```bash
~/start_jupyter.sh /media/sf_works/LaBr3
```

也可直接运行 `jupyter notebook --notebook-dir=/media/sf_works`。
启动脚本保持英文提示，`~/start_jupyter.sh --help` 可查看参数示例。
若浏览器未自动打开，复制终端显示的带 token 的本地网址。
选择 `LaBr3 (PyROOT)` 内核，点击代码单元后按 `Shift+Enter` 执行。
本机 apt 提供的 Notebook 6 可通过 Cell → Run All 从头执行全部代码；
Notebook 7 中对应 Run → Run All Cells。`Ctrl+S` 保存代码与结果。
前台运行时保留启动 Jupyter 的终端，结束时按 `Ctrl+C` 并确认关闭。
需要后台运行时可使用：

```bash
nohup ~/start_jupyter.sh /media/sf_works >> ~/jupyter.log 2>&1 < /dev/null &
```

也可在文件列表中创建新的 Python Notebook，选择同一内核来编写自己的代码。

Notebook 的启动方式可参考 [Jupyter 官方文档](https://docs.jupyter.org/en/latest/running.html)，
内核注册方式见 [IPython 官方文档](https://ipython.readthedocs.io/en/stable/install/kernel_install.html)。

推荐先打开 [02_real_run_energy_and_timing.ipynb](02_real_run_energy_and_timing.ipynb)。
它使用 PSD run0009 的真实排序数据，重新构建事件，输出通道能谱、能量门筛选、
时间差谱、峰拟合、残差及区间敏感性比较。参数集中在第 1 节，修改后可重新运行。
每次从头执行创建独立的 `results/` 子目录，保存图像、ROOT 直方图、CSV/JSON、
源码与参数快照，保留原始输入和历史事件文件。运行数据需保存在本机，路径可修改。
能量刻度未确认，使用存储单位；时间拟合报告两通道差值宽度及模型限制。

Notebook 02 已于 2026-09-28 使用本机注册的 `labr3-root` Jupyter 内核、
通过 nbclient 从头执行全部 9 个代码单元，并保留真实文本、表格和 PNG 输出。
修改共享 Python/C++ 实现后需重启内核再运行；修改筛选或拟合参数的重跑方法见末节。

只想查看事件构建修复过程时，可以用浏览器打开
[HTML 浏览版](01_event_building_and_regression.html)，无需安装 Jupyter。
代码默认折叠，点击“查看代码”展开；HTML 是当前保存结果的静态快照。

[01_event_building_and_regression.ipynb](01_event_building_and_regression.ipynb)
记录事件构建修复，包含项目流程、分组规则、Git 基线与当前源码的实际对比、
异常退出和已有输出保护示例，以及三套设备配置的完整回归结果。

文件保留执行输出，可以先直接预览。重新运行时，在 Jupyter 中选择能够导入
PyROOT 的 Python 内核，然后重启内核并从上到下执行；环境中还需有
`root-config`、GNU Make、C++ 编译器和 Git。测试函数复用项目 `tests/`，
输入样本及编译产物写入临时目录并自动清理。

Notebook 默认从当前目录向上定位项目。在其他位置启动内核时，设置首个代码单元的
`PROJECT_DIR`。旧版源码对比需要本地 Git 历史中的 `b096cd8` 提交。

Notebook 01 生成时本机未安装 Jupyter，因此使用当时的 Python 环境依次执行全部代码单元，
保存实际文本和 HTML 表格输出；验证方式及结果记录在 Notebook 元数据中。
该早期记录尚未进行 Jupyter 前端的交互显示验证。
