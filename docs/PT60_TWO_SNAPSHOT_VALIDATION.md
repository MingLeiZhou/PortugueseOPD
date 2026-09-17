# PT60 多断面公共证据验证

> 历史说明：本文件保存 2026-09-03 的中间实验结果，已由
> `PT60_REALITY_GAP_REMEDIATION_LOG.md` 和当前 `validation/temporal/` 输出取代。
> 其中 108.84% Belmonte–Sabugal 告警、六节点残余负荷和固定同相角多边界
> 均已修复，不应再引用本文件中的旧数值作为当前结果。

更新：2026-09-03

## 结论

本实验把 PT60 的时间验证扩展为三个主断面和一个受控敏感性断面：

- 2026-01-20 19:45 UTC：同步 E-REDES 冬季负荷，新增北部互联停运；
- 2026-03-23 20:15 UTC：全新的同步 E-REDES 负荷留出断面；
- 2026-09-02 20:15 UTC：新增互联投运，采用 2025 年同日同时刻的 E-REDES 季节匹配空间形状；
- 2026-09-02 冬季空间形状：仅用于量化负荷空间假设敏感性。

三个主断面均收敛、所有映射发电资产均满足铭牌约束、全国负荷均与 REN 总量严格对齐。修复多机组去重并融合 DGEG 官方资产后，3 月原有的 113.0 MW 水电残差已经归零；3 月和 9 月没有线路超过 100%，1 月保留一条 108.84% 的 60 kV 筛查告警。这些结果支持 PT60 是可复现、内部守恒且能承接独立节点负荷的研究基准。它们不支持 PT60 已复现运营商的逐线路潮流、边界相角或安全裕度。

## 输入证据

| 主断面 | REN 负荷 | REN 发电 | REN 净进口 | E-REDES 空间证据 | 边界拓扑 |
|---|---:|---:|---:|---|---|
| 2026-01-20 | 10,268.8 MW | 9,867.7 MW | +406.6 MW | 同步 395 站 | 7 边界母线、9 回路 |
| 2026-03-23 | 7,783.7 MW | 8,391.1 MW | −601.5 MW | 同步 390 站 | 7 边界母线、9 回路 |
| 2026-09-02 | 7,606.1 MW | 7,600.8 MW | +10.0 MW | 2025-09-02 同季同刻 394 站，按 REN 同时刻总负荷比率 ×1.04812 | 8 边界母线、10 回路 |

E-REDES 的全部 395、390 和 394 个变电站记录都能按官方设施代码映射到活动 PT60 母线，未匹配记录与未匹配功率均为零。这是数据连接完整性的强证据，但不是独立拓扑来源，因为 PT60 的 60 kV 设施清单本身也使用 E-REDES 公开记录。

E-REDES 变电站曲线只覆盖全国负荷的 76.3%--79.9%。剩余 20.1%--23.7% 包含 RNT 直供大用户和其他口径差异，目前仍按六个显式 RNT 负荷代理分配。因此同步断面的“节点级真实”仅适用于 E-REDES 覆盖部分。

## 交流潮流结果

| 指标 | 2026-01 冬季 | 2026-03 同步留出 | 2026-09 同季代理 |
|---|---:|---:|---:|
| ACPF 收敛 | 是 | 是 | 是 |
| 电压范围 | 0.9311--1.0092 pu | 0.9558--1.0106 pu | 0.9430--1.0108 pu |
| 最大线路负载率 | 108.84% | 88.24% | 85.37% |
| 超过 100% 的线路 | 1 | 0 | 0 |
| 最大变压器负载率 | 82.37% | 63.45% | 69.02% |
| 模型网损 | 239.79 MW | 144.93 MW | 116.88 MW |
| 模型网损/负荷 | 2.335% | 1.862% | 1.537% |
| REN 月度 RNT 网损率 | 2.36% | 2.44% | 2.47%（2026-08 近邻） |
| 网损率误差 | −0.025 pp | −0.578 pp | −0.933 pp |
| REN 净交换误差/负荷 | 2.28% | 1.79% | 1.47% |
| 映射资产铭牌越限 | 0 | 0 | 0 |
| 未映射发电残差 | 6.71 MW | 9.51 MW | 16.91 MW |

冬季网损率与 REN 同月 RNT 物理平衡接近。3 月和晚夏明显偏低，说明现有线路电阻、变压器损耗、网络口径和负荷空间代理仍使模型过于理想化。月度 RNT 与单时点 >=60 kV 模型不是完全同口径，因此该误差是近真实诊断而不是统计置信区间。

## 发现的问题

### 1. 发电资产去重缺陷与 DGEG 补充

2026-03 官方装机与 PT60 映射铭牌比较：

| 能源 | PT60 映射铭牌 | REN 官方装机 | 覆盖率 |
|---|---:|---:|---:|
| 水电 | 8,561.5 MW | 8,385 MW | 102.1% |
| 风电 | 4,907.4 MW | 5,453 MW | 90.0% |
| 光伏 | 4,080.1 MW | 4,884 MW | 83.5% |
| 天然气 | 4,378.9 MW | 4,353 MW | 100.6% |
| 生物质 | 799.9 MW | 685 MW | 116.8% |
| 其他火电 | 26.3 MW | 25 MW | 105.2% |

根因是原去重键将同一厂址、坐标取整后相同的无名称 `power=generator` 对象合并。例如 Gouvães 的 4×220 MW 曾只保留一台。修复后，水电和天然气覆盖分别恢复到 102.1% 和 100.6%。风电改用 DGEG 已获运行许可的风场分组，只有在 3 km 内存在 OSM 接入证据时才继承该母线；否则只允许接到 150 kV 及以上设施，20 km 内没有证据则保持未分配。光伏仅补入具有 DGEG 运行许可、与 OSM 名称不重复且距离超过 3 km 的设施，并明确标记 KVA 按单位功率因数近似为 MW。

3 月水电残差现为 0；总残差从 117.4 MW 降至 9.51 MW，其中其他火电 5.11 MW、电池 4.40 MW。REN 同月公布的其他火电装机仅 25 MW，却在该时刻记录 31.4 MW 出力，因此继续保留残差比把出力强塞给 26.29 MW 已映射资产更可信。剩余残差仍在 Rio Maior 400 kV 母线作为全国等值代理注入，但其规模已不足原值的 8.1%。

新增资产也暴露了一个有用的运行筛查问题：1 月按全国风电铭牌比例分配时，Belmonte--Sabugal 的一段 60 kV PDIRD 匹配线路达到 108.84%。3 月和 9 月不越限。该结果可能来自当地风电实际容量因子低于全国比例、接线状态差异或线路额定值近似；在没有区域风电实测和开关状态前保留为 FLAG，不通过移动资产或放宽额定值消除。

### 2. 多边界固定相角导致逐互联潮流不可靠

每个边界母线当前都作为 1.0 pu、0° 外部平衡点。模型会同时在不同葡西走廊产生较大的进口和出口：

| 断面 | 净交换 | 模型边界总进+总出 | 总交换/净交换绝对值 |
|---|---:|---:|---:|
| 2026-01 | +640.9 MW | 3,059.9 MW | 4.77 |
| 2026-03 | −462.5 MW | 3,086.4 MW | 6.67 |
| 2026-09 | +122.2 MW | 2,461.5 MW | 20.15 |

真实系统可能存在环流，但在没有西班牙侧相角或逐走廊遥测时，这些逐互联结果主要由代理阻抗和固定边界条件决定。它们不能作为真实线路潮流发布。只有边界清单和模型净功率平衡可以使用；逐走廊值必须保留 `MODEL_DERIVED_NOT_OBSERVED_PER_CIRCUIT` 状态。

### 3. 夏季负荷空间形状显著影响局部结果

把 2026-09 从冬季空间形状改为 2025 同季同刻形状后：

- 最低电压下降 0.0115 pu；
- 最大线路负载率下降 1.97 个百分点；
- 最大变压器负载率上升 9.12 个百分点；
- 模型网损增加 3.21 MW。

因此全国负荷总量对齐不足以验证节点潮流；季节空间分布会显著改变局部变压器与电压结论。

### 4. E-REDES 变电站容量筛查

390--395 个时点负荷中，每个断面有 3 个设施在 `carga-na-subestacao` 容量表中没有匹配记录：FANHÕES、SADO 和 MORTÁGUA。冬季 TURIZ 的有功 31.63 MW 略高于公开安装视在容量 31.5 MVA，差 0.13 MW；幅度很小，可能来自数据近似、时间口径或四舍五入，但保留为待核实项。其余有容量记录的节点均低于公开安装容量。

### 5. REE/ENTSO-E 独立边界证据尚未闭合

程序已经实现：

- REE e·sios 指标 557/561；
- ENTSO-E `A11` 葡西双向物理潮流；
- 与 REN 和 PT60 的统一符号、净进口及误差表。

当前环境没有 `ESIOS_API_TOKEN` 或 `ENTSOE_SECURITY_TOKEN`，所以独立来源状态为 `TOKEN_NOT_CONFIGURED`，明确记为 `PENDING`，没有算作验证通过。REN 净进口仍是留出量，但由于全国负荷与发电总量已经作为输入，模型与 REN 的净进口差主要等于模型网损误差，不能单独证明内部潮流正确。

## 可以支持与不能支持的主张

当前证据支持：

- PT60 能无损接入 390--395 个公开变电站时点记录；
- 三种负荷、发电、天气和两种边界拓扑下 ACPF 均可解；
- 全国有功严格守恒，映射发电始终满足公开铭牌；
- 未映射发电容量不会被强塞给小机组；
- 冬季模型网损与 REN 同月 RNT 损耗量级一致；
- 负荷空间和边界假设的影响能够被复现和量化。

当前证据不能支持：

- PT60 已复现真实逐线路潮流或节点电压；
- 多个外部边界之间的功率分配是真实的；
- 线路工程代理额定值等于运营商动态额定值；
- 2026-09 节点负荷是同步实测；
- 水电、风电、光伏资产清单已经完整。

因此，PT60 可以被描述为“经过多断面公共证据检验、数据守恒且局限透明的研究基准”，不能描述为“经运营商状态估计验证的真实电网模型”。

## 复现与输出

```bash
python portuguese_hv_network/src/run_temporal_validation.py --refresh
```

独立边界交叉验证需要：

```bash
export ESIOS_API_TOKEN=...
export ENTSOE_SECURITY_TOKEN=...
python portuguese_hv_network/src/run_temporal_validation.py --refresh
```

运行产物位于忽略目录 `portuguese_hv_network/outputs/temporal_validation/`：

- `multi_snapshot_comparison.csv/json`：三个主断面；
- `load_profile_audit.csv`：逐 E-REDES 设施映射；
- `substation_capacity_screen.csv`：时点负荷与公开变电站容量筛查；
- `generation_source_comparison.csv`：分能源发电与残差；
- `installed_capacity_coverage.csv`：PT60 与 REN 官方装机覆盖；
- `cross_border_evidence.csv`：PT60、REN、REE、ENTSO-E 边界证据；
- `modeled_boundary_flows.csv`：逐边界模型结果及非观测状态；
- `rnt_loss_benchmark.csv`：REN 月度 RNT 网损比较；
- `load_profile_sensitivity.csv`：同季与冬季空间形状敏感性；
- `line_loading_hotspots.csv`：各断面热点；
- `validation_findings.csv`：自动 PASS/FLAG/PENDING 诊断；
- `claim_evidence_matrix.csv`：论文主张边界。

## 公共来源

- [REN 日内发电、负荷和交换 API](https://datahub.ren.pt/en/api-instructions)
- [REN 月度 RNT 物理平衡](https://datahub.ren.pt/en/electricity/monthly-balance/)
- [E-REDES 变电站负荷曲线](https://e-redes.opendatasoft.com/explore/dataset/diagrama-de-carga-de-subestacao/)
- [E-REDES 变电站负荷与容量](https://e-redes.opendatasoft.com/explore/dataset/carga-na-subestacao/)
- [DGEG 发电设施地理信息](https://www.dgeg.gov.pt/pt/servicos-online/informacao-geografica/energia/energia-eletrica/)
- [REE e·sios API](https://api.esios.ree.es/)
- [ENTSO-E Transparency Platform](https://www.entsoe.eu/data/transparency-platform/)
- [REN 北部 400 kV 互联投运公告](https://www.ren.pt/en-gb/repository/noticias/news/portugal-and-spain-have-inaugurated-the-new-400-kv-electricity-interconnector-in-the-north-a-strategic-project-for-the-european-union)
