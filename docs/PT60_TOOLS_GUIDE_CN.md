> 当前核心版本：pt60-tools 0.4.3。PyPI 项目页 https://pypi.org/project/pt60-tools/ 提供数据、论文、网页与使用说明入口；以下本地安装步骤适用于源码开发，数据使用不需要克隆仓库。

# PT60 数据工具使用指南

当前工具版本 0.4.3，对应数据候选版 v2.1.0-rc2。工具包负责加载、检查、调用归档求解代码和查看结果；数据包独立存储。

## 1. 安装

在仓库根目录，使用 Python 3.13：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[solve,test]'
pt60 doctor
```

只看数据和地图时用 `python -m pip install -e .`，不必安装求解依赖。

## 2. 准备数据包

本项目已有 `data/releases/PT60-v2.1.0-rc2/`，工具在项目中自动发现它。在其他目录使用 `--release`（放在子命令前）或环境变量 `PT60_DATA` 指向已解压的数据包。

```bash
pt60 --release /path/to/PT60-v2.1.0-rc2 verify
```

也可以从本地压缩包导入至一个不存在的新目录；先校验整个归档 SHA-256，再解压并逐文件核查 manifest：

```bash
pt60 fetch data/releases/PT60-v2.1.0-rc2.tar.gz \
  --output output/imported-rc2 \
  --sha256 07727842080141300c4ddf180f82a4e7f9f66e3a444a30297d650f79a3486913
```

`fetch` 的 source 也接受明确提供的 HTTPS 文件链接。当前候选包下载地址为 https://grid.jczw.xyz/download ，SHA-256 为 `07727842080141300c4ddf180f82a4e7f9f66e3a444a30297d650f79a3486913`。永久公共存储 DOI 尚待登记。

## 3. 查看有哪些案例

```bash
pt60 info
pt60 cases --season WINTER
pt60 cases --json > output/cases.json
```

案例目录是冬夏两周 336 个主案例。观测区间为 15 分钟，案例按小时采样；时间表示 UTC 区间起点。分配方案的设备结果包含主方案 `AC_REVISED`、均匀交付点 `UNIFORM_PDE`、变压器容量权重 `CAPACITY_PDE`。

## 4. 用自己的输入构造新案例

```bash
pt60 init --output output/my-input
# 修改 output/my-input/scenario.json 与 loads.csv
pt60 solve --input output/my-input --output output/my-result
```

输入字段、单位、固定/初值出力和缺失值规则见[公共输入接口](../portuguese_hv_network/PUBLIC_MODEL_INTERFACE.md)。`init` 创建可运行的 1 月例子；`solve` 明确使用选定候选包的网络模板、资产表和归档代码。

结果目录包含 `summary.json`、完整求解网络、母线/线路/变压器/负荷/机组表、逐资产分配与来源审计表、`execution.log`。单案例输出必须不存在或为空；不会覆盖已有结果，也不能写入冻结数据包。

## 5. 复算归档案例和批量实验

```bash
pt60 replay --case PT60_2025_SUMMER_WEEK_JUL07_13_H000 --output output/replayed-case
pt60 batch --spatial --workers 3 --output output/pilot
pt60 batch --full --spatial --workers 3 --output output/full
```

默认 batch 是小规模试跑：不带 `--spatial` 为 18 个组合，带该选项为 22 个组合。全量 `--full --spatial` 为 1,358 个不重复组合。batch 调用原归档运行器，支持依赖匹配时续跑；进度见输出目录中的 `execution.log`。运行失败会返回非零退出码并指出日志位置。

## 6. 地图查看

```bash
pt60 view --port 8050
```

打开 `http://127.0.0.1:8050`。服务仅监听本机，直接读取 rc2 文件，不会改动数据。

- 选择验证周、时点和空间分配方案。
- 切换拓扑、线路负载、母线电压和同一线路的方案差值。
- 按电压筛选；搜索设施或设备 ID；点击设备读取来源及当前数值。
- 下载当前时点的设备结果 JSON。
- 拖动平移、滚轮缩放、点击“全网”复位。

颜色范围是显示规则；母线 0.95/1.05 p.u. 的色阶不是论文 0.90–1.10 p.u. 报告区间的替代。地图使用保留拓扑，有限结果计数不等于在运设备计数；归档中停运线路可能保留零结果。没有计算值的设备显示灰色。空间差值按照同一个线路 ID 比较。

把自己的求解结果加入查看器：

```bash
pt60 view --result-dir output/my-result
```

工具会检查结果已收敛、网络模板哈希和设备 ID 与选定数据包匹配，再增加“本地求解案例”选项。该选项不提供不存在的空间替代结果。地图几何与来源说明来自选定数据包，数值来自本地结果；若修改了线路参数，其完整修改记录应查看求解目录的审计表。

原 `portuguese_hv_network/site/` 保留为此前的开发界面；新版工具查看器通过归档读取避免混用旧数值。

## 7. Python API

```python
from pt60 import Dataset

data = Dataset('/path/to/PT60-v2.1.0-rc2')
assert data.verify()['status'] == 'PASS'
cases = data.cases('SUMMER')
case_id = cases[0]['case_id']
inputs = data.load_input(case_id)
fields = data.results(case_id, 'CAPACITY_PDE')
voltage = fields['bus_voltage']       # bus_id -> p.u. 或 None
loading = fields['line_loading']     # line_id -> % 或 None
data.replay(case_id, 'output/python-replay')
```

## 8. 原始数据重建与工具检查

原始采集和拓扑重建仍使用现有开发流水线：

```bash
python portuguese_hv_network/src/run_pipeline.py
```

这个入口更新开发目录的数据及模型，不是 rc2 的冻结回放。构建新的数据版本后，需要重新开展对齐、实验和发布审计。

```bash
python -m pytest tests/test_pt60_tools.py -q
python -m pip wheel --no-deps . --wheel-dir output/dist
```

核心命令仅覆盖数据读取与交流潮流复现。历史优化和学习实验已归档，不随核心软件分发。

## 现有 Cloudflare Web 界面

已增加 `pt60 export-web --output 新目录`，将冻结数据及可选的 `--result-dir` 本地求解结果接入现有 MapLibre 网站。使用流程与字段范围见 [Web 整合说明](PT60_WEB_INTEGRATION_CN.md)。网页提供方法切换、数据下载和本地复现命令；不在浏览器中执行 Python 求解。
