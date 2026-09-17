# PT60 数据工具与现有 Web 整合

现有 `portuguese_hv_network/site` 是原 Cloudflare 网站的代码目录。首页沿用 React / MapLibre 地图和原来的布局，数据入口改为 `public/data/pt60`。网站已发布到现有 Cloudflare 域名 `https://grid.jczw.xyz/`；没有推送 Git 远端。

## 使用流程

在项目根目录安装工具，使用现有解压 rc2 包：

```sh
python -m pip install -e '.[solve]'
pt60 verify
pt60 export-web --output portuguese_hv_network/site/public/data/pt60
```

输出目录必须不存在。更新已有导出时，先把旧目录移动到网站 `public` 之外的备份位置，再执行导出。源数据校验、设备 ID 检查和结果极值检查全部通过后，导出器才将新目录放到指定位置。

```sh
cd portuguese_hv_network/site
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

打开本地开发服务显示的 URL。首页支持夏季/冬季周选择、UTC 日期与时刻切换、PDIRT 主方法与均匀/容量加权 PDE 对照、设备检索、母线电压、线路负载率、最高负载线路定位及数据下载。两个周各 168 个每小时时点；每个时点代表一个 15 分钟区间，标签为 UTC 区间起点。

页面的“数据与复现”区提供 `init → 修改输入 → solve → view` 的操作命令，以及当前归档案例的 `replay` 命令。下载的归档回放输入采用归档 runner 的格式；自定义 `solve` 使用 `pt60 init` 生成的公开输入目录，两者格式不同。

## 加入本地求解结果

```sh
pt60 init --output work/my-case
# 编辑 work/my-case 中的输入
pt60 solve --input work/my-case --output work/my-result
pt60 export-web --result-dir work/my-result --output work/web-with-local-result
```

导出器要求本地结果收敛、模型模板 SHA-256 与 rc2 一致、母线和线路 ID 集合完全一致。检查完成后，将整个新导出目录移至站点的 `public/data/pt60`，即可在“本地求解案例”组查看。当前工作区的演示导出含 336 个归档案例和一个已经求解的公开示例，共 1,009 组结果；其中冻结包结果为 1,008 组。本地结果明确标记为 `local-result`，没有归档 `replay` 入口。

## 数据范围与准确性

- 地图母线/线路/变压器/设施/机组资产几何及标识来自 rc2；不沿用旧 Web 的结果值和季节定额。
- 冻结的逐设备字段只有母线电压幅值与线路负载率。未保存的逐设备 P/Q、电流及变压器负载率不填充；页面中的最高变压器负载率来自对应实验汇总。
- 灰色为缺失值。NPZ 中的零线路负载率可能包括停运线路，不能由零值推断该线路处于运行状态。
- 机组图层是映射资产清单，不表达每个时点的机组出力或投运状态。冻结包没有详细 OSM 场区图层，所以该控件在当前数据模式下隐藏。
- 静态 CSV 下载保留科学数据值，移除实验日志中的本机 `spatial_result_path`。
- 本地自定义结果只复用模板几何；修改后的线路参数应查阅其求解输出中的设备表。
- 地图底图仍使用现有的 OpenStreetMap 在线瓦片与字体；完全离线时可使用 `pt60 view` 自带查看器。

## 四项交付的网页材料

从项目根目录先运行 `uv build --out-dir output/pt60-tools-0.4.3`，再运行 `python scripts/prepare_publication.py`。后者核对冻结数据包 SHA-256，生成下载资源、当前论文及其本地链接附件，以及网页共用的版本清单。Cloudflare 单资源大小限制由透明分段传输处理；服务端 `scripts/worker-entry.js` 将静态分段原生流式拼接，用户从 `/download` 获得的仍是原始 tar.gz 文件。线上完成下载后须核对 SHA-256，校验值见项目页。

## 验证与构建

```sh
python -m pytest tests/test_pt60_tools.py -q
cd portuguese_hv_network/site
npm run test:pt60
npx tsc --noEmit
npm run build
```

构建前检查全部数据路径，避免代码构建成功但页面数据为 404。生成的数据仍按现有规则被 Git 忽略；代码交付时必须附此生成步骤。旧 `snapshot-data.test.mjs` 用于旧季节导出，新的 `pt60-data.test.mjs` 验证 rc2 全部方法、设备 ID、结果极值、案例/方法错配拒绝和几何中无旧结果。

项目入口为 `/project`，连接数据包、Python、地图与论文。`/optimization` 仅保留归档实验结果，不再作为主导航或构建前提。
