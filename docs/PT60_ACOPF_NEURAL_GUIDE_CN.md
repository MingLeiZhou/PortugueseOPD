# PT60 AC-OPF 与神经网络工具

这套工具在 rc2 的观测驱动 AC-PF 数据之外建立独立优化基准，提供 AC-OPF 求解、扰动场景、GridSFM 图数据转换、独立复算、预训练推理、微调及测试。原 rc2 归档不修改。

## 1. 安装与输入

在项目根目录运行：

```sh
python -m pip install -e '.[solve,ml]'
pt60 init --output work/input
# 按公开接口编辑 work/input
pt60 solve --input work/input --output work/pf
pt60 opf-policy --output work/policy.json
```

`ml` 固定使用 Microsoft GridSFM 提交 `1ca775fd436d7ce013a1c0ab946e61ac7ef59ad6`。公开权重为 Hugging Face `microsoft/GridSFM_Open` 的 `gridsfm_open_v1.1.pt`；本次文件 SHA-256 为 `f8a4396122e603e8303afdebe3b093819c0f64dac0878394aed0bd63205fd831`。上游加载器使用 `weights_only=True` 并校验权重内部哈希。

```sh
hf download microsoft/GridSFM_Open gridsfm_open_v1.1.pt --local-dir work/checkpoints
```

## 2. 优化问题如何定义

输入为已经求解的 pandapower 网络 JSON，加上可编辑 `policy.json`：

- 固定时点负荷 P/Q 及固定 PQ 发电注入；已有非参考跨境等值注入保持给定。
- 对聚合 PV 机组优化有功、无功及相关母线电压。有功范围为 0 至已有可用铭牌容量，无功范围沿用模型中的显式上下限。
- 优化唯一参考外部边界，默认 P/Q 范围均为 ±3,000 MW/Mvar。这是实验边界，不是观测到的跨境可用输电容量。
- 母线电压范围 0.9–1.1 p.u.，变压器分接头固定。线路和变压器两端施加视在功率约束；线路定额由模型额定电压与允许电流换算。该口径与 PF 地图的实际电流负载率不同。
- 机组目标项默认 `50 P + 0.01 P²`；参考边界默认 `80 P + 0.01 P²`。P 以 MW 计，系数按 €/MWh 与 €/MW²h 的实验量纲解释。负边界 P 对应出口，其线性项可产生收入。全部系数明确标为合成基准参数，不是实测报价；`generator_cost_overrides` 支持按聚合机组名替换。
- 不优化跨时段储能、水库能量或机组组合。停运案例将指定聚合机组移出服务，负荷变化同步缩放 P/Q，线路降额缩放既有视在功率上限。

```sh
pt60 opf --model work/pf/PT60_PUBLIC_EXAMPLE_solved.json \
  --policy work/policy.json --output work/opf
```

输出包含优化输入、参数审计、求解网络、设备结果、PPC 矩阵及 `report.json`。状态为 `LOCALLY_SOLVED` 只表示非凸 AC-OPF 的局部解，不保证全局最优。求解失败保留失败记录，不改限额、不削减负荷来包装成功。

独立检查阈值：节点 P/Q 最大残差 ≤ 0.001 MW/Mvar，发电 P/Q 和支路视在功率最大越限 ≤ 0.001 MW/Mvar/MVA，电压越限 ≤ 1e-5 p.u.，角差越限 ≤ 1e-5 度。求解器“收敛”与这些检查分别记录。

当前 SciPy 移除了稀疏矩阵 `.H` 属性，而 PYPOWER 的视在功率 Hessian 仍使用该接口；工具仅在本进程优化调用期间提供等价 `getH()` 兼容属性，退出后恢复，不修改安装包文件。

## 3. 场景生成和图格式

```sh
python benchmarks/run_pt60_opf_scenarios.py \
  --model work/pf/PT60_PUBLIC_EXAMPLE_solved.json --output work/scenarios \
  --checkpoint work/checkpoints/gridsfm_open_v1.1.pt --workers 2
pt60 graph-export --opf work/opf --output work/case.pyg.json
pt60 graph-check --graph work/case.pyg.json --output work/roundtrip.json
```

场景脚本生成负荷 ±5%、线路降额 10%、一组聚合发电停运，并保留每个场景的结果或失败原因。每个成功场景导出规范的 GridSFM/OPFData 列布局：母线 4 列、发电机 11 列、线路 9 列、变压器 11 列。功率按系统 baseMVA 转为标幺，角度为弧度，成本系数相应换算。

图中的负荷节点为固定净 PQ 需求，可能包含固定发电或边界注入形成的负值。未给定的机器 MBASE 使用系统基准，MBASE 不参与网络方程。无法由该图格式表达的非对称支路或支路并联电导会被拒绝导出。

输入发电 Pg/Qg/Vg 使用常量 0/0/1；求解标签仅在 `solution` 中，预测加载器只读取 `grid`。导出时比较网络导纳矩阵，`graph-check` 进一步仅从图的输入重建模型，冷启动重新求解，并比较目标值（相对差异要求 <1e-4）和全部物理约束。原始参考解不用于初始化。

## 4. 神经网络推理与微调

```sh
pt60 predict --graph work/case.pyg.json \
  --checkpoint work/checkpoints/gridsfm_open_v1.1.pt --output work/prediction
pt60 graph-check --graph work/case.pyg.json \
  --prediction work/prediction/prediction.json --output work/corrected.json
```

推理输出电压、相角、机组 P/Q、支路 P/Q 与可行性概率。工具独立从预测电压与发电量重算功率平衡、发电/电压/支路约束，概率不能代替检查。`seconds` 为包含图预处理和前向计算的 CPU 推理时间，不含加载权重或后续优化。

预测作为优化初值时，仅对初值裁剪至变量边界，优化问题不变。若该初值无法收敛，则明确记录 `fallback_to_cold_start: true`，回到冷启动；总时间包含失败尝试，不能据此宣称加速。

微调使用包含 `train`、`validation`、`test` 三个路径列表的 JSON 清单，路径相对清单文件。所有来自同一原始工况的扰动必须放在同一组，工具检查原始输入哈希，并在可用时检查案例 ID 与时间。示例见 `output/opf/training-splits.json`。

```sh
pt60 finetune --manifest splits.json \
  --checkpoint work/checkpoints/gridsfm_open_v1.1.pt \
  --output work/training --epochs 20 --lr 0.00001
```

采用固定随机种子 60、CPU、batch size 1、AdamW 与上游联合监督/物理损失，按验证损失选择检查点。测试集不参与选择。输出 `pt60_gridsfm.pt`、逐轮历史、数据划分和独立测试中预训练/微调模型的并列审计。

本次属于小样本试验：5 个训练图共享一个冬季原始案例，另以独立夏季案例验证、另一冬季时点测试。可以验证代码和实验链路，不足以证明跨时段或跨电网泛化。精度和可行性以实际生成的报告为准，不将未通过物理检查的预测作为可行调度。

## 来源

- [Microsoft GridSFM](https://github.com/microsoft/GridSFM/tree/1ca775fd436d7ce013a1c0ab946e61ac7ef59ad6)，MIT 许可；模型架构、预训练权重加载和微调损失均复用上游实现。
- [公开模型权重](https://huggingface.co/microsoft/GridSFM_Open)。
- PT60 输入取自冻结 rc2 及其公开建模接口；优化假设和学习实验单独保存，不改变数据论文的观测验证结果。

## 本次实测结果（2026-09-13）

七个 AC-OPF 案例均通过独立物理检查。基准图冷启动复算的目标值相对差异为 7.27e-14，导纳矩阵差异为零。预训练预测初值未能收敛，校正流程回退冷启动后通过检查，未证明加速。

20 轮微调耗时约 280 秒，验证损失选中最后一轮。独立测试时点为冬季周 H096；训练原始案例与夏季验证案例均不包含此时点。

| 指标 | 预训练 | PT60 微调 |
|---|---:|---:|
| 电压 MAE / p.u. | 0.04455 | 0.01778 |
| 聚合机组 P MAE / MW | 56.58 | 41.99 |
| 最大节点 P 残差 / MW | 20010.91 | 13533.99 |
| 最大支路视在功率越限 / MVA | 131546.62 | 106997.69 |
| CPU 推理时间 / 秒 | 0.740 | 0.728 |
| 独立物理检查 | 未通过 | 未通过 |

这些大残差按原 PT60 网络方程计算，不能因电压误差或目标函数数值接近而忽略。因此本检查点用于方法研究与后续改进，不可直接作为可行调度解；需要经过约束优化校正。小样本训练与独立原始时点测试已经完成，但模型精度与物理可行性仍需扩大训练数据、分析病态支路和改进物理一致性方法。

微调检查点 SHA-256：`6ef0fb60f5faf0444346e3047749a7abcae98c1f775c7f8ed523de64d87f26b7`。

### 已识别的上游适配缺口

固定提交的 GridSFM 支路监督/热限损失采用 `|x| > 0.01` 筛选条件；在本次独立测试网络中，5,001 条有效支路有 2,656 条不满足该条件。其部分损失计算还对 `r²+x²` 使用 `1e-6` 下限，本网络有 494 条支路低于该阈值。这说明上游学习用的数值处理不能直接当作 PT60 全网络物理可行性证明，也是后续适配需要核查的具体问题；本次保留上游算法作为可复现对照，独立审计始终使用未经这种裁剪的原网络方程。
