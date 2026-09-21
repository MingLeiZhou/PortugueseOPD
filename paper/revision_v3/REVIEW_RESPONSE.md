# 审稿意见逐项处理记录

发布标识：SimPT60-2026.09.21-r1。正文：paper/paper_final_edited.md，与 paper/PT60_Sep16.MD 同步。

| 请求 | 实质处理 | 可核查输出 |
|---|---|---|
| 唯一冻结快照并发布 | 11 月库字节不改；核心输入重组并离线复现；CORE-3783 和 N1-3787 明确区分；代码提交、环境、哈希、复现命令 | release.json、SHA256SUMS、MONTHLY_MANIFEST.json、model_field_diff.csv、replay.py |
| 四处缺失引用 | 官方文件/网页归档核对；APA 接入与储能来源分开；REE 限定为历史名称证据；R 转用明确降级；Estoi 规划库存与实时状态区分 | citation_evidence_ledger.csv、resistance_evidence_overlay.csv；正文 §2.2.3 |
| 重建结果验证 | 整站五折留出，394 站、8,541 观测对、22 时点；网络路径、地理距离、容量基线同条件比较 | heldout_station_folds.csv、heldout_station_predictions.csv、heldout_spatial_summary.csv |
| N−1 故障集合 | 六组完整成员哈希相同；4,943 分段减156停运、2母排、14缺失结果；1,421线路组+228变压器组；并联及不完整身份透明 | nminus1_membership.csv、nminus1_exclusions.csv、nminus1_universe_counts.csv |
| 已有违规恶化 | 同一物理回路成对差；0.1/1/5百分点阈值；既有1pp恶化排除后辅助通过9,032 | nminus1_case_worsening.csv、nminus1_worsening_summary.csv、各时点detail |
| 观测/PDIRT/全国代理 | 全31,492案例；消费和供给分母分开；逐月八能源类别、逐时点、区域/母线接收位置 | proxy_monthly_summary.csv、generation_proxy_monthly_by_energy.csv、load_proxy_regional_shares.csv、high_proxy_intervals.csv |
| 运行代理消融 | 11配置×22时点=242有效计算全部收敛；Q/PV/负荷补偿/电抗器/分接控制；发现旧分接器无有效类型，另增有效Ratio实验 | operational_ablations/protocol.json、case_results.csv、summary.csv、raw_cases |
| 静态资料与历史日期 | 日期门控范围、未知处理和未回溯设备明确；规划年份不充作投运日期 | source_availability_matrix.csv；正文Table 5 |
| 已有工作对比 | PyPSA-Eur、SimBench、SimPT60按7维度对照，不作首次/完整性排名 | 正文Table 1 |
| Top-20稳定性 | 全4,943线路的264实验频率；固定时点12假设内持续性与跨时点频率区分 | top20_frequency_all_lines.csv、top20_frequency_by_state.csv、top20_membership.csv |
| 缩写 | 展开DGM、RARI、PDIRT、PDIRD的葡语名称 | 首次正文出现处 |
| GeoPandas引用 | 作者、2026年份、卷130和文章102495由官方CITATION及Crossref双核；12月为分配期次，不当作网络首发日 | evidence/geopandas_CITATION.md、geopandas_crossref.json |
| 130kV样本 | 表注、正文和图4标记n=7，仅供参考 | 正文Table 11及Figure 4 |

## 新发现与不能夸大的结论

- 3,787 与 3,783 的差额来自四个 PI_FEITOSA 派生节点；工作库还有额定值等字段修正，不只是母线行数变化。
- 网络路径留出法优于全局容量基线，但未胜过地理邻站法，不能据此宣称拓扑已独立验证。
- 原分接器类型为空；关闭该控制的近零响应是实现限制，不是物理结论。有效Ratio模型作为独立变体，不篡改历史月库。
- 最大风电/最大负荷有17/15个旧增量通过案例恶化超过1百分点。
- 全国代理份额小，但2025年5月电池发电代理份额90.58%；不能用全国均值遮蔽小类别。
- 没有完整历史开关、检修、逐台Q能力和节点潮流真值；这些缺口明确保留，未伪造补齐。
