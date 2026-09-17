# PT60 连续周修订实施记录

日期：2026-09-06。目的：按公开接入证据修复热点，保持冬夏两周原输入做前后对照，再同步论文图表。

## 已完成

1. 核验 336 个 E-REDES 负荷缓存与 14 个 REN 日曲线，原失败记录为空。
2. 沿 E-REDES 线路代码确认：1259 是 PC Trancoso–Trancoso 配电支路；1261 是约 11.4 km 的风电送出支路；6202 是 Zambujal–Venda Nova II 的串联走廊。没有凭负载率增加回路数。
3. 按 APA 接入说明修订四项风电记录，涉及 Trancoso 两项、Sernancelhe 两项；新增映射已有的 32 MW。新增 Moimenta 接入为明确声明的 400 kV 汇集等值。
4. 修复独立电池与母线发电聚合的重复有功注入，新增对实际 pandapower 注入总量的检查。
5. 完成两周 336 个修订时点，以及原峰值时刻的原版复现、规划定额及中间 GIS 接点隔离对照；共保存 342 条求解记录。
6. 更新正文图 4、5、6、表 7、数据来源表、验证方法及解释；原三个日窗口作为原版本辅助结果保留。

## 结果

| 指标 | 夏季修订周 | 冬季修订周 |
| --- | ---: | ---: |
| 收敛 / 样本 | 168/168 | 168/168 |
| 净交换 MAE | 78.5 MW | 146.5 MW |
| 净交换 RMSE | 83.5 MW | 159.4 MW |
| 平均绝对误差 / 同期总负荷 | 1.16% | 1.71% |
| 最大线路负载率 | 130.38% | 130.44% |
| 阈值触发采样点 | 49 | 80 |

原冬季热点 LINE:000884 在原峰值时刻由 136.72% 降至 49.60%；冬季全网最大值由 136.72% 降至 130.44%，最大值转移到 LINE:004013（SEIA–LORIGA）。夏季热点仍为约 130.38%。只有在未证实的 6202 中间接点隔离假设下，该热点降至 84.13%，因此没有把这种连接改动作为正式修复。

系统净交换 MAE 未改善。局部接入修复与全国总量误差不是同一个验证问题，论文保留这个不利结果。实际有功注入与 REN 分配目标的最大差异小于 4×10⁻¹² MW；这是守恒检查，不是实测预测误差。

## 公开来源

- [APA AIA 3403，第 5 页](https://siaia.apambiente.pt/AIADOC/AIA3403/parecerca_3403202192134944.pdf)：Trancoso 风电专用 60 kV、约 11 km 送出线。
- [APA PPA 421](https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/421)：Sernancelhe 经 60 kV 线路至 Moimenta 汇集站。
- [APA PPA 407](https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/407)：Moimenta/Douro Sul 与 Armamar 的 400 kV 连接。
- [E-REDES RARI 2024，PDF 第 56、65 页](https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf)：1259 与 6202 长度及年度最大电流 59 / 113 A。这两个电流不是额定限值，也不属于本次求解时刻。
- [PDIRD-E 2020 附件 B](https://www.erse.pt/media/340hrot0/proposta-pdird-e-2020_anexo_b.pdf)：2025-12-31 规划参数，仅用于单独的季节定额参照实验。

## 复现与检查

```bash
python portuguese_hv_network/src/test_hotspot_revision.py
python portuguese_hv_network/src/run_seasonal_hotspot_revision.py --workers 3
python paper/scripts/generate_pt60_seasonal_figures.py
```

模型、资产表、342 条求解结果、336 组配对、固定线路诊断及来源指纹位于 `portuguese_hv_network/outputs/temporal_validation/seasonal_revision/`。公开接口可通过 `model_path`、`generators_path` 使用这组配套输入。运行器核对输入与实现指纹，防止源数据或程序改变后静默复用旧结果。

回归检查覆盖源身份匹配、原表不变、铭牌总量不变、新增映射数量、实际注入守恒，以及连接敏感性不改变线路长度和回路数。图形输出为 PNG/SVG；字形、对齐和碰撞检查使用 temp 中的图形 PDF，不生成论文 PDF。

## 保留的问题

- 夏季 6202 实际接线状态未核实；仍然不能将负载率写为现实过载。
- 剩余风电空间出力为分能源总量分配，无同步单机调度；修订后 Trancoso 电流仍高于不同年份的公开峰值。
- Sernancelhe 场内集电网没有逐资产重建，无功控制设置在本次受控比较中保持原值。
- Trancoso 部分 DGEG 许可日期与 APA 扩建阶段描述存在差异，尚未确认逐期投运日期。
- 两处热点由原结果选出并参与修复，不能宣称为修复后的独立留出验证。
- 新研究结果尚未写入原 v2.0.0 发布归档；未发布到前端或替换 DOI 数据。
