# Kaggle / Zenodo 发布准备（SimPT-Power）

本目录只放发布辅助文件；数据本身不入 Git。

| 文件 | 用途 |
|---|---|
| `01_simpt_power_quickstart.ipynb` | 快速上手：各层数据、高压网地图、31,492 个断面、PTD 与地理低压网、公开观测表 |
| `02_hv_power_flow.ipynb` | 高压潮流：重算全国峰值断面（与冻结结果差 < 1e-9 个百分点）、负载率地图、1 月时序、400 kV N-1 示例 |
| `make_notebooks.py` | 生成上面两个 notebook（改 notebook 请改这里再重新生成） |
| `requirements.txt` / `requirements-full-lock.txt` | 计算用的依赖版本（pandapower 3.5.2 等）；r2 发布包里的 `requirements-lock.txt` 只记了 python 和 duckdb |
| `dataset-metadata.json` | Kaggle 命令行上传用的元数据（先改 `YOUR_KAGGLE_USERNAME` 和 DOI；默认私有） |

## 步骤

1. 生成开放子集（不改冻结发布）：`python3 src/build_open_bundle.py` → `output/simpt_power_release/open_bundle_r2/`（约 260 MB，Parquet + JSON + SHA256SUMS）。
2. Zenodo：上传冻结发布 `data/releases/SimPT-Power-2026.09.30-r2/` 的全部文件，再加上开放子集（建议打成一个 `open_bundle_r2.zip`，或逐个上传）。上传前先建草稿，拿到预留 DOI，填进论文第 6 节和 `dataset-metadata.json`。
3. Kaggle 数据集，二选一：
   - 网页新建数据集 → 从网址导入 Zenodo 上 `open_bundle_r2.zip` 的下载链接；
   - 或命令行：把 `dataset-metadata.json` 复制到 `open_bundle_r2/`，运行 `kaggle datasets create -p output/simpt_power_release/open_bundle_r2 --dir-mode zip`。
4. Kaggle notebook：新建 notebook → 导入 `.ipynb` → 添加上面的数据集 → 设置里打开 Internet（安装 pandapower 3.5.2 用）→ 运行全部。

本地测试：`SIMPT_DATA=output/simpt_power_release/open_bundle_r2 jupyter nbconvert --to notebook --execute kaggle/02_hv_power_flow.ipynb`
