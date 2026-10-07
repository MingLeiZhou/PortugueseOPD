# Kaggle / Zenodo 发布准备（SimPT-Power）

本目录只放发布辅助文件；数据本身不入 Git。

| 文件 | 用途 |
|---|---|
| `01_simpt_power_quickstart.ipynb` | 快速上手：各层数据、高压网地图、31,492 个断面、PTD 与地理低压网、公开观测表 |
| `02_hv_power_flow.ipynb` | 高压潮流：重算全国峰值断面（与冻结结果差 < 1e-9 个百分点）、负载率地图、1 月时序、400 kV N-1 示例 |
| `03_mv_lv_power_flow.ipynb` | 中低压潮流：城区中压电缆网（MVROOT:00298）pandapower 求解；地理低压四线制网络（PTD 1106D1002700）前推回代，与库内结果一致 |
| `04_hv_graph_learning.ipynb` | 图学习样例：高压网转为图张量，以 2026 年 1 月逐线路负载率为目标，对比逐线路均值、逐线路线性回归与简单 GNN（含未见线路泛化） |
| `make_notebooks.py` | 生成上面四个 notebook（改 notebook 请改这里再重新生成） |
| `requirements.txt` / `requirements-full-lock.txt` | 计算用的依赖版本（pandapower 3.5.2 等）；r2 发布包里的 `requirements-lock.txt` 只记了 python 和 duckdb |
| `dataset-metadata.json` | Kaggle 命令行上传用的元数据（先改 `YOUR_KAGGLE_USERNAME` 和 DOI；默认私有） |

## 步骤（当前发布 r4）

1. 构建发布与开放子集（不改已冻结的 r3.1 及更早发布）：先跑 `scripts/rebuild_r4.sh` 重建工作库，再按顺序运行 `python3 src/build_simpt_power_r4.py db|clean|validate|compress|freeze|bundle`。冻结发布在 `data/releases/SimPT-Power-2026.10.04-r4/`，开放子集为 `output/simpt_power_release_r4/open_bundle_r4.zip`（Parquet + JSON + SHA256SUMS；hv/ 与 r3.1 相同）。
2. Zenodo：`ZENODO_TOKEN=... python3 scripts/zenodo_new_version_r4.py` 新建草稿版本（保留 r3.1 未变文件，替换变化文件），在网页上核对后手动发布。r4 的 DOI 为 [10.5281/zenodo.23132707](https://doi.org/10.5281/zenodo.23132707)（所有版本：10.5281/zenodo.23067064）。
3. Kaggle 数据集，二选一：
   - 网页新建数据集 → 从网址导入 Zenodo 上 `open_bundle_r4.zip` 的下载链接；
   - 或命令行：解压 `open_bundle_r4.zip`，把 `dataset-metadata.json` 复制到 `open_bundle_r4/`，运行 `kaggle datasets create -p <解压目录>/open_bundle_r4 --dir-mode zip`。
4. Kaggle notebook：新建 notebook → 导入 `.ipynb` → 添加上面的数据集 → 设置里打开 Internet（安装 pandapower 3.5.2 用）→ 运行全部。

本地测试：`SIMPT_DATA=<解压目录>/open_bundle_r4 jupyter nbconvert --to notebook --execute kaggle/02_hv_power_flow.ipynb`
