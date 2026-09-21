# SimPT60 Narrative Restructuring and Reduction Plan

## Part 1 — One-sentence thesis

SimPT60 将碎片化的葡萄牙公开电网、资产和运行数据转化为一个可追溯、版本化的 60–400 kV 网络及 31,492 个连续 15 分钟 AC 潮流案例，并以结构一致性、敏感性、外部时空比较和数值闭合证明其研究可用性，同时明确它不等同于运营商数字孪生或设备级真值。

## Part 2 — Core narrative

- **Problem:** 现有公开数据分别描述网络、资产或聚合运行量，无法直接形成葡萄牙 60–400 kV、带连续时间索引的可计算 AC 网络案例。
- **Construction:** SimPT60 以稳定对象标识连接静态拓扑、资产、15 分钟负荷与发电、边界交换和 AC 求解结果，并保存来源及转换记录。
- **Reproducibility:** 数据产品冻结为明确版本；关键拓扑规则、参数代理、时间对齐、分配规则和质量控制可追踪，CORE-3783 与仅用于 N−1 的 N1-3787 必须区分。
- **Validation:** 研究层面的可信度来自结构覆盖、参数与空间分配敏感性、全国时间序列比较、市镇/变电站空间比较、整站留出实验和 AC 功率闭合，而非设备级遥测对照。
- **Application:** 一个确定性压力情景和六个代表时点的全元件 N−1 面板足以证明数据集能够支持批量情景计算和相对风险筛查。
- **Interpretation:** 结果适合可复算研究案例、方法比较和假设敏感性分析；参数代理、历史设备状态和节点空间分配仍限制绝对设备级解释。
- **Scope:** 论文的核心产品是数据集及其证据边界；逐设备审计、控制搜索、完整故障成员表和发布工程细节应留在仓库，而不是扩展成第二条论文主线。

### Backbone

| Backbone element | Scientific function | Required evidence |
| --- | --- | --- |
| A. Problem | 说明公开数据缺口 | 与 PyPSA-Eur、SimBench 等范围比较；葡萄牙 60 kV 与连续 AC cases 缺口 |
| B. Construction | 说明数据集如何形成 | 数据源、拓扑、资产/时间映射、AC case generation、provenance/QC |
| C. Dataset | 说明发布了什么 | 3,783 buses、4,943 lines、228 transformers、31,492 cases、时间范围、发布结构 |
| D. Validation | 说明为何可用于研究 | 结构一致性、敏感性、外部时间/空间比较、留出测试、数值闭合 |
| E. Application | 证明可计算用途 | 一个 stress scenario；一个 representative-time N−1 panel |
| F. Scope | 防止过度解释 | public-data-derived；非设备遥测、非运营商 digital twin、非正式合规模型 |

### Quantitative diagnosis

正文在 References 前约 38,300 字符。Methodology 约占 35%，Validation 约占 31%，两者合计约 66%；其中 §2.2.3、§4.3、§4.6 和 §5.4 是主要压缩点。补充材料约 16,500 字符，Notes S4–S5 的事故分析约占 42%。建议正文减少约 7,000–8,000 字符（18–21%），补充材料减少约 6,000–7,000 字符（36–42%）。

## Part 3 — Main-text reduction plan

### Section-level classification

| Current section | Role | Class | Action | Why | Revised role |
| --- | --- | --- | --- | --- | --- |
| Abstract | 贡献、数据规模、验证和边界摘要 | CORE | SHORTEN | 当前信息完整；只需保留一条边界声明 | 150–200 words 的独立摘要 |
| §1 Introduction | 研究缺口与贡献 | CORE | SHORTEN | Table 1、Table 2 已承担大量范围信息，正文可减少重复 | gap → product → evidence → scope |
| §2.1 Public Data Scope and Standardization | 来源范围与标准化 | CORE | SHORTEN | §2.1.1 对每个门户和来源的解释偏长，Table 3 已汇总 | 一段来源逻辑 + Table 3 + 一段归档原则 |
| §2.2.1 Buses and Lines | 拓扑构建基础 | CORE | KEEP | 简短且直接支撑重建方法 | 保留 |
| §2.2.2 Transformers and Parameters | 连接与参数规则 | CORE | SHORTEN | 阈值在 Table 4 和 Supplement S2 中重复 | 正文讲逻辑；数值阈值留表格 |
| §2.2.3 Evidence Applicability and Historical Availability | 证据适用性与历史状态边界 | SUPPORTING | MOVE TO SUPPLEMENT | 设备 ID、PDF 页、工程号和电阻 forensic audit 打断主线 | 正文保留 1 段 + 精简 Table 5；证据台账移仓库 |
| §2.3 Asset Mapping and Time-Series Alignment | 资产、时间和功率分配 | CORE | SHORTEN | Table 6 已逐项总结，正文与表格重复 | 保留时间语义、守恒式和代理处理；删逐项复述 |
| §2.4 AC Case Generation | 案例组装与求解 | CORE | SHORTEN | 月库写入和程序输出细节可转仓库 | 保留 case definition、solver、31,492-case execution rule |
| §2.5 Provenance and QC | 可追溯性与质量控制 | CORE | SHORTEN | 数据库表名与事务实现属于 Supplement/README | 正文保留分层逻辑、原子写入和阻断性 QC |
| §3.1–§3.4 Dataset data, coverage and composition | 数据产品内容与规模 | CORE | MERGE | 四个短节可整合为“network and time-indexed cases”两节 | 网络组成 + 时间覆盖/案例完整性 |
| §3.5 File Organization and Distribution | 发布包结构 | SUPPORTING | SHORTEN | Table 9 与 §2.5、Table S5、Note S2 重复 | 正文一段说明主库、月库、provenance 和 release |
| §3.6 Frozen Release and Model-Version Reconciliation | 结果版本边界 | CORE | SHORTEN | 版本差异影响解释，必须保留；字段级 diff 不需在正文 | 一段说明 CORE-3783 与 N1-3787 及适用章节 |
| §4.1 Validation Design and Evidence Levels | 验证逻辑 | CORE | SHORTEN | 可压缩为外部、敏感性、内部三类证据 | 作为 Validation 开场的一段 |
| §4.2 Structural and Computational Consistency | 结构与数值一致性 | CORE | SHORTEN | 部分统计同时出现在 Figure 4、Table 10 和正文 | 保留 Figure 4 与关键计数；压缩逐项解释 |
| §4.3 Parameter and Spatial-Allocation Sensitivity | 假设敏感性 | CORE | SHORTEN | Figure 5 已表达主要结果，Table 11 是完整数值展开 | 正文保留 2–3 个结论；详细表移 Supplement |
| §4.3.1 Persistence of Ranked Hotspots | 二级热点稳定性分析 | SUPPORTING | MOVE TO SUPPLEMENT | 是敏感性分析的派生结果，不是数据集主 claim | 主文只留一句“热点成员比全网排序更不稳定” |
| §4.4 External Temporal Agreement | 外部时间验证 | CORE | KEEP | 直接支撑聚合时间一致性 | 保留 Figure 7 和主要指标 |
| §4.5 External Spatial Agreement | 外部空间验证 | CORE | KEEP | 直接支撑空间合理性及其证据等级 | 保留 Figure 8；与 Table 12 紧邻 |
| §4.5.1 Station-Held-Out Test | 重建结果的留出验证 | CORE | SHORTEN | 比聚合相关更接近空间重建有效性，但算法细节过多 | 正文一段方法、一段结果；精确 fold/weight/bootstrap 移 Supplement/仓库 |
| §4.6 Generation Scope and Spatial Proxies | 代理依赖与范围差异 | CORE | SHORTEN | 当前 2,143 字符、两图一表，成为独立支线 | 保留 2–4 个关键百分比、集中代理母线与 Figure 10；其余移仓库 |
| §4.7 Limitations | 统一证据边界 | CORE | KEEP | 应成为完整免责声明的主要位置 | 保留完整版本；其他章节只做局部短提示 |
| §5.1 Analysis and Visualization Workflow | Web 工作流说明 | NON-ESSENTIAL | MERGE | 不支撑数据集的主要科学 claim | 合并为 §5 开头 2–3 句 |
| §5.2–§5.3 Stress Scenario and Results | 确定性用途展示 | SUPPORTING | MERGE | 方法、结果和网页描述可合并 | 一个 concise stress-scenario subsection |
| §5.4 Representative-Time N−1 | 批量故障计算展示 | CORE | SHORTEN | 定义和结果重要；补救控制与设备审计已在 Supplement | 一段定义、一段结果、Figure 12、Table 17 |
| §5.5 Interpretation | 应用边界 | SUPPORTING | MERGE | 单独成节过短 | 合并到 §5.4 末段或 §4.7 |
| §6 Conclusions | 贡献、证据、用途和限制 | CORE | SHORTEN | 当前五段中部分重新叙述 Validation | 压为四段：product、validation、application、scope/future work |

### Main figures and tables

| Item | Role | Class | Action | Rationale |
| --- | --- | --- | --- | --- |
| Table 1 | 与已有数据集比较 | CORE | KEEP | 建立 gap，避免“首次”空泛断言 |
| Table 2 | 产品范围和解释边界 | CORE | KEEP | 一页式 dataset summary |
| Table 3 | 数据来源矩阵 | CORE | KEEP | 方法可理解性所需 |
| Figure 1 | 拓扑重建概念 | CORE | KEEP | 减少文字解释 |
| Table 4 | 拓扑阈值 | SUPPORTING | KEEP | 复现关键规则；正文不再重复数值 |
| Table 5 | 静态来源与历史日期 | CORE | SHORTEN | 直接回答历史状态边界；压成 4–5 类对象 |
| Table 6 | 映射与时间规则 | CORE | KEEP | 复现核心规则；删正文逐项重复 |
| Table 7 | 案例和 solver 设置 | SUPPORTING | KEEP | 最小计算复现所需 |
| Figures 2–3 | 网络地图、时间覆盖 | CORE | KEEP | 数据集论文的核心描述图 |
| Table 8 | 数据集规模 | CORE | KEEP | 核心 counts |
| Table 9 | 发布包逻辑结构 | SUPPORTING | MOVE TO SUPPLEMENT | 与 §2.5、Table S5、Note S2 重复 |
| Figure 4 | 结构与参数证据 | CORE | KEEP | 集中呈现覆盖和参数证据 |
| Table 10 | 结构和计算完整性 | CORE | SHORTEN | 保留最关键统计，删图中已呈现的信息 |
| Figure 5 | 参数/空间敏感性 | CORE | KEEP | 支撑 robustness claim |
| Table 11 | 敏感性完整数值 | SUPPORTING | MOVE TO SUPPLEMENT | Figure 5 和正文已给核心结果 |
| Figure 6 | 热点 persistence | SUPPORTING | MOVE TO SUPPLEMENT | 二级稳定性分析；正文只保留结论 |
| Figure 7 | 外部时间验证 | CORE | KEEP | 核心验证图 |
| Figure 8 | 外部空间验证 | CORE | KEEP | 核心验证图 |
| Table 12 | 验证证据汇总 | CORE | KEEP | 汇总样本、CI 与证据等级 |
| Table 13 | 整站留出结果 | CORE | KEEP | 直接检验空间恢复；方法压缩即可 |
| Figure 9 | 发电范围与平衡定义 | SUPPORTING | MOVE TO REPOSITORY | 范围差异可由正文和数据重现；不是主要验证结果 |
| Table 14 | 逐月代理占比 | SUPPORTING | MOVE TO REPOSITORY | 机器可读 CSV 更合适；正文只报范围和峰值月份 |
| Figure 10 | 观测与代理分布 | CORE | KEEP | 一张图概括依赖程度和空间集中 |
| Figure 11 | 压力情景 | SUPPORTING | KEEP | 简洁证明 scenario usability |
| Table 15 | 压力情景精确数值 | SUPPORTING | MOVE TO REPOSITORY | Figure 11 加正文关键值已足够 |
| Table 16 | N−1 故障集合摘要 | SUPPORTING | MOVE TO SUPPLEMENT | 正文用一句 1,421 + 228 = 1,649 即可；规则由精简 S9 承担 |
| Figure 12 | 全元件 N−1 面板 | CORE | KEEP | 主要应用证据 |
| Table 17 | 六时点精确汇总 | SUPPORTING | KEEP | 为 Figure 12 提供精确数值；删除重复 Table S10 |

### Main-text editing rules

1. 将完整边界声明集中在 Abstract、Introduction 结尾、§4.7 和 Conclusion；其他位置只保留与当地结果直接相关的一句限制。
2. 表格已经给出阈值或规则时，正文只解释为什么采用该规则，不逐项重写数值。
3. 主文不再出现文件哈希、绝对路径、逐设备 ID、完整排除成员、cache/path mapping 或 replay 命令。
4. 设备级案例只在它改变主结论时出现；仅用于内部排错的设备名移到 repository audit note。
5. 每一小节只保留一个中心结论；若内容只是对上一张表或图的再叙述，则删除。

## Part 4 — Supplement reduction plan

### Item-level classification

| Supplement item | Role | Class | Action | Reason |
| --- | --- | --- | --- | --- |
| Table S1 | 原始字段到标准语义 | CORE | KEEP | 数据字典的紧凑入口 |
| Table S2 | 电气参数库 | CORE | KEEP | 参数代理复现所需 |
| Table S3 | 映射与审计字段 | SUPPORTING | SHORTEN | 保留关键证据字段；完整 schema 移 repository |
| Table S4 | QC 与失败处理 | CORE | KEEP | 复现及结果解释所需 |
| Note S1 + Table S5 | 数据库 schema | SUPPORTING | SHORTEN | 压成数据层级、粒度和连接键；逐表字段放 data dictionary/README |
| Note S2 | 版本和发布协调 | CORE | SHORTEN | 保留 CORE-3783/N1-3787、4 buses、228→230 和适用结果；精确哈希及 replay 指令移 repository |
| Note S3 narrative | 运行代理消融 | SUPPORTING | SHORTEN | 保留设计、有效配置和主要影响；删失败配置排错过程 |
| Table S6 | 运行代理消融数值 | SUPPORTING | KEEP | 无功边界敏感性是重要限制证据 |
| Figure S1 | 运行代理消融图 | NON-ESSENTIAL | DELETE | Table S6 已完整包含数值，图只重复两个响应维度 |
| Note S4 targeted N−1 | 定向 70-case 审计 | SUPPORTING | MERGE | 与 Note S5 合并为一个 concise contingency supplement |
| Figure S2 | 定向 N−1 响应 | SUPPORTING | KEEP | 与主文全元件面板不同，提供设备响应形态 |
| Table S7 | 70-case 分类计数 | NON-ESSENTIAL | DELETE | 4 行数字已在正文段落和 Figure S2 caption 中完整出现 |
| Post-contingency control prose | 三个北部案例控制搜索 | NON-ESSENTIAL | MOVE TO REPOSITORY | 属于第二篇事故分析的支线，不支撑数据集核心 claim |
| Table S8 | 三案例无功范围搜索 | NON-ESSENTIAL | MOVE TO REPOSITORY | forensic sensitivity；不应占 Supplement 篇幅 |
| Godigana transfer paragraph | 单一案例转供代理 | NON-ESSENTIAL | MOVE TO REPOSITORY | 内部工程诊断，和主文代表面板关系弱 |
| Note S5 introductory prose | 全元件 N−1 定义 | CORE | MERGE | 与精简 Note S4 合并为最终 S4 |
| Table S9 | 故障集合与排除规则 | CORE | SHORTEN | 保留选择规则和数量；14 个 ID、完整 membership、SHA-256 移 repository |
| Figure S3 | 代表时点 N−1 图 | NON-ESSENTIAL | DELETE | 与正文 Figure 12 的 PNG SHA-256 完全相同 |
| Table S10 | 六时点 N−1 汇总 | NON-ESSENTIAL | DELETE | 与正文 Table 17 逐字一致 |
| Incremental criterion prose | 已有越限恶化定义 | SUPPORTING | SHORTEN | 保留公式、盲点和 1 pp 诊断定义；删重复边界说明 |
| Table S11 | 已有违规恶化 | SUPPORTING | KEEP | 提供增量指标盲点的必要定量证据 |
| 完整 contingency membership、per-case outputs、阈值扫描 | 机器级复现 | NON-ESSENTIAL | MOVE TO REPOSITORY | CSV/JSON 比 PDF 更适合查询和版本校验 |

### Strong Delete Candidates

| Item | Why removable | Risk of deletion | Recommended destination |
| --- | --- | --- | --- |
| Figure S3 | 与 Figure 12 是同一二进制图像 | 无；正文已保留 | DELETE |
| Table S10 | 与 Table 17 逐字一致 | 无；正文已保留 | DELETE |
| Figure S1 | Table S6 已包含完整数据，图只重复两列 | 较低；失去快速视觉比较 | DELETE，必要时仓库保留 PNG |
| Table S7 | 66/3/1 分类已在相邻正文和图注出现 | 极低 | DELETE |
| §5.1 两段 Web 工作流 | 产品演示细节不支撑核心科学 claim | 较低；少量使用说明消失 | 合并为 §5 开头两句；完整说明留 README |
| Main Table 15 | Figure 11 和正文已有关键数值 | 较低；失去全部五点精确表 | MOVE TO REPOSITORY |
| Main Figure 9 | 范围差异已由正文与 Figure 7/10 解释 | 中等；少一个范围对比视觉 | MOVE TO REPOSITORY，正文保留关键范围差异 |
| Main Table 14 | 逐月数值适合 CSV，不适合主线 | 中等；读者不能直接看全部月份 | MOVE TO REPOSITORY；正文报告 min–max 和异常月份 |
| Note S4 control search + Table S8 | 属于事故后控制研究支线 | 中等；失去诊断深度 | MOVE TO REPOSITORY audit report |
| Godigana 个案推导 | 单一工程排查案例，不能推广 | 低 | MOVE TO REPOSITORY audit report |
| Note S2 精确 SHA-256 和命令块 | release engineering 内容 | 低；Supplement 不再离线给出命令 | MOVE TO RELEASE README / release.json |
| §2.2.3 设备 ID、PDF 页和工程号 | 证据台账而非叙事 | 中等；详细证据不在 PDF | MOVE TO `citation_evidence_ledger.csv` 和 evidence README |

### Merge candidates

| Items | Decision | Result |
| --- | --- | --- |
| Table S7 + Table S8 | 不合并为新表 | S7 删除；S8 移 repository，避免把两种不同实验硬拼 |
| Main Table 16 + Table S9 | MERGE conceptually | 主文只保留一句总数；Supplement S9 保留精简选择/排除规则 |
| Main Table 17 + Table S10 | KEEP main / DELETE supplement | 精确汇总只出现一次 |
| Figure 12 + Figure S3 | KEEP main / DELETE supplement | 主图只出现一次 |
| Notes S4 + S5 | MERGE | 一个 Supplement S4：fault-set definition、targeted diagnostic、incremental worsening |
| Figure 5 + Table 11 | KEEP figure / MOVE table | 主文展示结论；Supplement 保存完整敏感性数值 |
| Figure 11 + Table 15 | KEEP figure / MOVE table | 主文展示应用趋势；CSV 保存精确值 |
| §3.5 + Table 9 + Table S5 | MERGE information hierarchy | 正文一段发布结构；Supplement 一张精简 schema 表；完整 manifest 在 repository |

### Proposed Supplement scope after reduction

- **S1 Data dictionaries and parameter rules:** Tables S1–S4；精简后的 schema summary。
- **S2 Release and version reconciliation:** CORE-3783 与 N1-3787 的必要解释；其余链接到 release manifest。
- **S3 Additional validation and sensitivity:** Table 11、热点 persistence、station-held-out 方法摘要、Table S6；删除重复消融图。
- **S4 Detailed contingency diagnostics:** 精简 Table S9、Figure S2、增量恶化公式和 Table S11；其余转 repository。

## Part 5 — Proposed lean structure

```text
1 Introduction
  1.1 Research gap
  1.2 SimPT60 contribution and scope

2 Methodology
  2.1 Public data sources and standardization
  2.2 Static network reconstruction
  2.3 Asset and time-series mapping
  2.4 AC case generation
  2.5 Provenance and quality control

3 Dataset Overview
  3.1 Static network and assets
  3.2 Time-series coverage and AC cases
  3.3 Release structure and model variants

4 Validation
  4.1 Validation design and structural consistency
  4.2 Parameter and allocation sensitivity
  4.3 External temporal agreement
  4.4 External spatial agreement and station-held-out test
  4.5 Dependence on spatial proxies
  4.6 Limitations

5 Application
  5.1 Deterministic grid-stress scenario
  5.2 Representative-time full-element N−1 demonstration

6 Conclusions

Supplementary
S1 Data dictionaries and parameter rules
S2 Release and model-version reconciliation
S3 Additional validation and sensitivity
S4 Detailed contingency diagnostics

Repository documentation
- release.json, SHA256SUMS, requirements lock and replay commands
- citation evidence ledger and document/page mapping
- field-level CORE-3783/N1-3787 differences
- full schema/data dictionary
- complete N−1 membership, exclusions and per-case outputs
- control-search and Godigana audit notes
- monthly proxy tables and flagged intervals/buses
```

## Editorial decision

该稿不需要再增加实验。最有效的改进是把论文重新定位为“可追溯数据集 + 研究层验证 + 两个简洁用途展示”，并移除审计报告式写法。建议优先执行四项操作：压缩 §2.2.3；将 §4.3.1 移 Supplement；将 §4.6 从两图一表压成一图和两段；合并并削减 Supplement Notes S4–S5。这样可以达到约 18–21% 的正文缩减和约 36–42% 的 Supplement 缩减，同时保留全部核心 claim、关键敏感性、复现规则和证据边界。
