# PT60 Sep16 补充表

本文件配套 [正文](PT60_Sep16.MD)，参数和规则对应论文归档版本。

<a id="supplementary-table-s1"></a>

**Table S1——原始字段到标准语义。**

| 来源 / 原始字段 | 标准语义或目标字段 | 转换 / 处理 | 关联与缺失规则 |
| --- | --- | --- | --- |
| E-REDES energia | 节点 p_mw | 15 min kWh / 250 → MW | codigo_subestacao 匹配设施；不插值 |
| E-REDES datahora；data / hora | UTC 区间起点 | 终点标签减 15 min；Lisbon → UTC | 重复标签按站平均，记录歧义 |
| REN source_date + source_index | calendar.timestamp_utc | 按 Lisbon 自然日和日内序号建日历 | 序号区分重复小时 |
| REN series_name + value_mw | 分能源 / 负荷 / 储能全国量 | 保留 MW；类型内分配 | 缺失必需序列则案例失败 |
| rede_at_teste.properties.id / tensao_de | source_line_id / voltage_kv | 保留业务键；电压标准化为 kV | 限定本版电压层 |
| 原始 geometry / 坐标 | geo 几何；lon / lat | 存储 WGS 84；距离 EPSG:3763 | 无有效几何须记录 |
| OSM way / relation / power tags | source_id；回路身份；资产类型 | 显式节点分段；保留关系键 | 不以线条相交推定连接 |
| 公开装机与 rating | nameplate_mw / sn_mva | 功率 / 容量单位标准化 | 无公开值则代理并标证据 |
| 归档文件 URL / bytes / hash | provenance.raw_files | SHA-256；相对归档路径 | 保留原始文件定位信息 |

**表注。** 语义表不宣称每个源文件使用同一字段名。main.eredes_load / main.ren_dispatch / raw_eredes.rede_at_teste 字段已在工作主库只读核对；详细原始字段以来源归档为准。

<a id="supplementary-table-s2"></a>

**Table S2——电气参数库。**

| 电压（kV） | r（Ω/km） | x（Ω/km） | c（nF/km） | Imax（kA） | 性质 |
| --- | --- | --- | --- | --- | --- |
| 60 | 0.1093 | 0.368854 | 9.2 | 0.606 | 电压级工程代理 |
| 130 | 0.07 | 0.33 | 9.8 | 0.8 | 电压级工程代理 |
| 150 | 0.06 | 0.32 | 10.0 | 0.85 | 电压级工程代理 |
| 220 | 0.04 | 0.285 | 11.0 | 1.2 | 电压级工程代理 |
| 400 | 0.025 | 0.25 | 12.0 | 2.0 | 电压级工程代理 |

**表注。** 表内是夏季基准默认值，非所有线路的最终值；可匹配公开回路的线路使用逐字段来源覆盖。电缆修正系数 r ×0.65、x ×0.35、c ×20、Imax ×0.90。按季节再施加 Table 5 中的定额倍率。来源 model_config.json。

| 变压器电压组合（kV） | 默认 S（MVA） | vk（%） | vkr（%） |
| --- | --- | --- | --- |
| 400/220 | 450.0 | 12.0 | 0.3 |
| 400/150 | 450.0 | 12.0 | 0.3 |
| 400/60 | 170.0 | 12.0 | 0.35 |
| 220/150 | 250.0 | 12.0 | 0.35 |
| 150/130 | 140.0 | 12.0 | 0.45 |
| 220/60 | 170.0 | 12.0 | 0.4 |
| 150/60 | 170.0 | 12.0 | 0.45 |
| 130/60 | 126.0 | 12.0 | 0.45 |

**变压器表注。** 默认容量与阻抗为工程代理；公开容量优先，分电压组合总容量不足时以 REN 汇总向上校准并保留系数。档位参数见 Table 5；Estoi 等逐设备覆盖值不能被此默认表覆盖。

<a id="supplementary-table-s3"></a>

**Table S3——映射与分配审计字段。**

| 存储位置 | 实际字段 / 记录 | 含义 | 使用限制 |
| --- | --- | --- | --- |
| grid.generators | source_id；generator_id；bus_id | 来源资产及目标母线 | bus_id 缺失表示未映射 |
| grid.generators | match_distance_m；bus_assignment_rule | 匹配距离与规则 | 距离是证据指标，非连接证明 |
| grid.generators | hierarchy_dedup_status | 电厂—机组去重状态 | 保留归并依据 |
| grid.generators | available_from_utc；asset_evidence_source | 投运日期与资产证据 | 日期缺失按稿件规则可用并标记 |
| grid.buses | endpoint_match_distance_m；source_status | 端点—设施匹配距离与状态 | 派生母线不得标为直接测量 |
| grid.lines | parameter_status；r_status / x_status / c_status / max_i_status | 整体与逐参数证据 | 部分支撑不等于全部实测 |
| grid.transformers | match_distance_m；capacity_calibration_factor | 连接匹配与容量校准 | 校准不验证逐台容量 |
| main.interval_calendar | eredes_alignment_status；timestamp_utc | 时间对齐歧义与 UTC 起点 | 不抹去缺失或重复时段 |
| scenario.load_operating_points | observed_p_mw；ren_residual_p_mw；q_mvar | 直接负荷、残差与无功 | 区分观测与分配 |
| scenario.generator_operating_points | dispatch_fraction；dispatch_status；dispatch_target_source | 能源分配与来源角色 | 代理机组出力不能当作遥测 |
| monthly_model.audit_bundles | records 中的分配 / 边界 / 损耗记录 | 逐案例权重、剩余量与守恒检查 | 以包内键为准；不虚构统一 weight 列 |
| provenance.raw_record_locator | entity_type / entity_id；raw_table / raw_record_id | 派生实体回溯原始记录 | 需与归档版本共同使用 |

**表注。** 字段名经工作主库 schema 与月库建表代码核对；具体字段值随版本变化。

<a id="supplementary-table-s4"></a>

**Table S4——质量控制与失败处理。**

| 层级 / 检查 | 阈值或条件 | 严重程度 / 行为 | 记录位置 |
| --- | --- | --- | --- |
| 静态 / 标识与端点 | 设备键有效；端点存在 | 阻断无效连接 | 静态审计及 provenance |
| 静态 / 电压与长度 | 同电压连接；长度 >0；无自环 | 阻断无效支路 | 拓扑审计 |
| 静态 / 连通性 | 分量、孤立点、边界清单 | 记录；按范围解释孤立对象 | topology_summary.json |
| 时序 / 必需输入 | UTC 键一致；必需序列存在 | 失败保留错误；不插值 | monthly_model.cases.error |
| 时序 / 负荷守恒 | 节点和与 Consumption + Storage 差 ≤0.11 MW | 不满足则阻断案例 | 案例分配审计 |
| 时序 / 发电容量 | 已投运映射资产 0≤P≤装机 | 超出映射量记显式全国代理 | 发电分配审计 |
| 求解 / AC 收敛 | NR 容差 10⁻⁶ MVA；主流程最多 50 次 | 不收敛保留错误和失败状态 | monthly_model.cases |
| 求解 / 有功闭合 | 记录 Pgen + Pnet − Pload − Ploss | 数值残差报告，不充当外部真值 | audit_bundles；验证汇总 |
| 筛查 / 电压及热 | 0.90–1.10 p.u.；一般 100% 定额 | 标记风险；不删除已完成案例 | cases 与状态数组 |
| 外部 / 缺失与统计口径 | 共同时间 / 地区；成对排除缺失 | 保留异常月份；报告样本数 | 外部比较结果与 CI |
| 写库 / 原子性 | 摘要、数组和审计包同事务 | 失败整体回滚，保留错误 | 月库事务与错误记录 |
| 发布 / 完整性 | 预期、完成、数组、审计包计数一致 | 有失败不发布为完整月份 | run_manifest；文件哈希 |

**表注。** N−1 的电压、线路和变压器阈值另见正文第 5.4 节；主案例质量控制与事故筛查不可混用。
