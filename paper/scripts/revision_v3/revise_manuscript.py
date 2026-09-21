from pathlib import Path
import re,json,pandas as pd,hashlib
ROOT=Path(__file__).resolve().parents[3];D=ROOT/'paper/revision_v3';P=ROOT/'paper';s=(D/'manuscript_before_revision.md').read_text()
def before(marker,text):
 global s
 assert marker in s,marker
 s=s.replace(marker,text+'\n\n'+marker,1)
def after(marker,text):
 global s
 assert marker in s,marker
 s=s.replace(marker,marker+'\n\n'+text,1)
def table(title,headers,rows):
 return '**'+title+'**\n\n| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)+'\n'
comparison=table('Table R1——与既有公开模型的范围及可追溯性比较。',['维度','PyPSA-Eur','SimBench','SimPT60'],[
['地理对象','欧洲 ENTSO-E 区域真实网络重建','德国代表性基准网络','葡萄牙大陆及葡西边界重建'],['电压范围','交流 ≥220 kV；HVDC','低压至超高压，多电压等级','60、130、150、220、400 kV'],['时间组织','小时级需求/可再生可用率；可聚合','全年 15 min 负荷/发电剖面','2025-05-01—2026-03-24；15 min'],['可计算性','PyPSA 优化与运行研究模型','潮流基准与标准化算例','31,492 个归档交流潮流案例'],['参数证据','公开网络/资产与类型假设；流程公开','代表性网络设计与类型参数','逐字段区分直接来源、转用和工程代理'],['来源追踪','数据源、版本和构建工作流','文档、网络代码、剖面标识','原始文件哈希、稳定对象 ID、案例审计包'],['用途边界','欧洲系统规划；不覆盖葡萄牙 60 kV 配网','可比性基准；不表示葡萄牙真实地理','聚合一致性与代理筛查；不等于设备遥测']])
comparison+='\n比较依据原论文及官方文档：PyPSA-Eur ([Hörsch et al., 2018](#ref-Horsch2018); [官方说明](https://pypsa-eur.readthedocs.io/en/latest/))；SimBench ([Meinecke et al., 2020](#ref-Meinecke2020); [文档 v1.0.0](https://simbench.de/wp-content/uploads/2020/01/simbench_documentation_en_1.0.0.pdf))。官方网页核对日期为 2026-09-21；本表比较用途与数据组织，不作“首次”或完整性排名。'
before('## 1.1 Motivation',comparison)
s=s.replace('APA 与项目公告用于补充储能、投运和接入证据 [CITATION NEEDED]。','APA 项目记录用于接入合理性核查：AIA 3403（2021 年 7 月，PDF 第 5 页）记载 Trancoso 的 60 kV、11.4 km 送出线；PPA 421 与 PPA 407 的“项目名称”字段分别记载 Sernancelhe–Moimenta 60 kV 和 Douro Sul–Armamar 400 kV 连接 ([APA, 2021](#ref-APA3403); [APA, n.d.-a](#ref-APAPPA421); [APA, n.d.-b](#ref-APAPPA407))。两份 PPA 网页的投运日期均空缺，不能据此赋予历史可用性；这些证据支撑接入审计变体，不表示核心快照的最近母线映射已经逐项改正。Casal da Cortiça 储能的容量和接入月份依据 DGEG 项目记录 ([DGEG, 2025](#ref-DGEGCasal2025))。')
s=s.replace('PDIRT 提供交付点参考剖面','PDIRT（Plano de Desenvolvimento e Investimento da Rede Nacional de Transporte，国家输电网发展与投资规划）提供交付点参考剖面',1)
s=s.replace('PDIRD 提供部分配电回路','PDIRD（Plano de Desenvolvimento e Investimento da Rede Nacional de Distribuição，国家配电网发展与投资规划）提供部分配电回路',1)
s=s.replace('REE 文档用于互联清单核对 [CITATION NEEDED]。','REE《Interconexiones eléctricas》（2012 年 9 月，第 10 页）仅用于历史互联走廊名称对照，不能证明案例日期的投运、检修状态或全部回路身份 ([REE, 2012](#ref-REE2012))。模型的 7 个边界母线代表 9 回 220/400 kV 回路等值，未纳入的较低电压互联不能算作不存在。')
s=s.replace('RARI 边界名称与设施','RARI（Regulamento do Acesso às Redes e às Interligações do Setor Elétrico，电网及互联接入规章）边界名称与设施',1)
s=s.replace('其 DGM 分量','其 DGM（Diagrama de Geração de Mercado，市场发电曲线）分量',1)
# Each remaining missing citation is closed with a source and explicit applicability limit.
s=s.replace('[CITATION NEEDED]','[见第 2.2.3 节证据范围]')
# Replace the conductor paragraph rather than treating a transferred parameter as measured.
line=next(l for l in s.splitlines() if '电阻' in l and '2.2.3' in l)
s=s.replace(line,line.replace('[见第 2.2.3 节证据范围]','([EDP Distribuição, 2019](#ref-Tocha2019))'))
evidence='''### 2.2.3 Evidence Applicability and Historical Availability

四处原缺失引用已形成逐项证据表 `citation_evidence_ledger.csv`，包含文档版本、文件 SHA-256、PDF 页与印刷页、适用设备、字段和未被来源支持的推断。APA AIA 3403 对应 GEN:01017/01018 的 Trancoso 送出走廊；PPA 421 对应 GEN:01099/01100 的 Sernancelhe–Moimenta 接入。将后一接入压缩到 Moimenta 的 400 kV 母线仍是集电网络等值，不能由“60 kV 接入”记录直接证明。

导线电阻依据 2019-02-19 的 PE Tocha II–Tocha 60 kV 项目说明（工程号 2800-19C007374），PDF 第 3、5 页，印刷第 2、4 页。文档给出的 ACSR 导线总截面为 326.1 mm²、铝截面 264.4 mm²、钢截面 61.7 mm²，20°C 电阻为 0.1093 Ω/km。现有代码以名义截面 A 按 R=0.1093×325/(A n) 转用，n 由“2x”前缀取 2，否则取 1，再按串联段长度加权。该截面缩放、前缀解释及向其他导线族的转用均为工程假设；没有逐线验证交流效应、运行温度、材质或束导线含义。核心库中 1,517 行的历史 `CONDUCTOR_DERIVED` 标签因此在发布的 `resistance_evidence_overlay.csv` 中统一降级为“项目参数转用工程假设”，另 3,426 行继续标为工程代理。原始数值和字节哈希不变，不能再将前者计作直接电阻观测。

Estoi 的三台 126 MVA、150/60 kV 设备可在 REN PDIRT 2016–2025 规划附件 6 的 PDF 第 50、52、54、56 页（印刷第 8、10、12、14 页）逐项核对，分别对应计划的 2016、2018、2020、2025 年末清册 ([REN, 2015](#ref-REN2015Estoi))。这支持 378 MVA 的计划库存及电压组合，但同母线、相同参数、同时可用的三台并联等值仍是 N−1 模型假设。没有单位级投运与开关记录，不能把“2025 年末”当作实际投运日期。

静态来源的下载版本与案例历史日期分别管理。仅存在明确日期规则的资产按时点切换；其余静态线路、变压器并未完成逐设备历史回溯。Table R2 列明可用性处理，因此本文应称为“在冻结静态网络上施加历史输入的案例”，而不是历史网络状态的完整重演。
'''
avrows=[['E-REDES / OSM 线路','来源文件哈希冻结','多数没有设备有效日期','原静态在运标记固定；普通线路不按历史时点回溯'],['变压器 / RARI / REN','核心 228 行；N−1 单列修正','缺少单位级历史','容量和接入固定；Estoi 三台同时可用是等值假设'],['Minho–Galicia 新互联','REE 2026-07-02 公告','2026-07-02，日分辨率','两行 LINE:009732/009814 在所有研究案例中停用'],['其他葡西互联','冻结 7 边界、9 回路等值','无检修及开关历史','保持固定；2012 年地图只核对名称'],['PDIRD / PDIRT 参考','2020-07 / 2024-12 规划资料','清册/季节剖面，非实测时点','参数和残差权重代理；不据此推断实时投运'],['发电/储能','冻结 1,190 行清单','366 行有日期、824 行缺失','已知日期门控；未知按可用并标记'],['Casal da Cortiça BESS','DGEG 2025-06-18 项目记录','仅确认 2025 年 6 月接网','6 月 1 日为月初代理；12 MVA→12 MW 假设功率因数 1']]
evidence+=table('Table R2——来源版本、有效日期、投运状态和未知处理。',['对象','来源版本','有效日期证据','案例处理与限制'],avrows)
evidence+='\n新互联投运日依据 [REE 官方公告](https://www.ree.es/es/sala-de-prensa/actualidad/nota-de-prensa-interconexiones/2026/07/espana-y-portugal-inauguran-la-nueva-interconexion)。完整字段、规则与版本见 `source_availability_matrix.csv`。BESS 的确切日和实际功率因数未公开，其不确定性最多影响该 12 MW 资产的空间分配及 6 月上旬可用性，不改变强制守恒的全国发电总量。'
before('## 2.3 Asset Mapping and Time-Series Alignment',evidence)
s=s.replace('上述并联设备配置依据公开技术记录 [见第 2.2.3 节证据范围]。','上述计划库存依据 REN PDIRT 2016–2025 附件 6（PDF 第 50、56 页）；实际同时在运状态及并联等值的限制见第 2.2.3 节 ([REN, 2015](#ref-REN2015Estoi))。')
s=s.replace('**表注。** 正文与图表采用 2026-09-16 归档验证快照；2026-09-21 只读检查发现工作主库 grid.buses 已有 3,787 行，不能与该论文快照混用。正式分发时须固定主库、月库和静态模型版本。','**表注。** 本表为发布的 CORE-3783 变体；N−1 使用 N1-3787 变体。两者的对象级差异、适用章节和固定哈希见第 3.6 节，不将工作库当作论文唯一网络。')
release='''## 3.6 Frozen Release and Model-Version Reconciliation

论文唯一发布标识为 **SimPT60-2026.09.21-r1**，固定入口为 [GitHub Release](https://github.com/MingLeiZhou/PortugueseOPD/releases/tag/SimPT60-2026.09.21-r1)。发布由一个复现核心包、11 个不可变月库压缩文件、论文 PDF、`release.json` 和 `SHA256SUMS` 共同构成。`release.json` 给出完整代码提交号、环境、输入与输出哈希；月库清单同时保存压缩前后的 SHA-256、案例数和原 run manifest，避免只有压缩包名称而不能验证内容。代码提交是本次复现与审计实现的提交，不冒充未保存的原始计算提交；原计算使用的模型和发电资产文件则由原 run manifest 的哈希固定。环境完整锁定于 `requirements-lock.txt`，本次校验使用 Python 3.13 与 pandapower 3.5.2。

CORE-3783 保留 31,492 个历史案例所用的 3,783 母线、4,943 线路、228 变压器；静态模型 SHA-256 为 `57f3be5e9488bfad43e9c3504c5d703885a14457f0bcbde21994dff942225013`，发电清单为 `3cc22e06f28c35c3d80e530e898a34808eb3e17c6685029c9d0953063e7326bc`。正文第 3、4 节及原参数/分配扰动均对应此核心；第 5.4–5.6 节使用明确单列的 N1-3787 修正模型，六个时点分别归档其完整已求解基准 JSON。

工作库多出的 4 行不是新变电站，而是 Lanheses–Feitosa 的 `PUBLIC_LANHESES_PI_TOPOLOGY_SPLIT` 派生节点：原 `BUS:JUNCTION:60:00479`、`00480`、`00481`、`02655` 分别复制出带 `:PI_FEITOSA` 后缀的同址节点。该操作把此前因共用几何节点而并接的两条回路拆开，使 Deocriste–Lanheses 与 Lanheses–Feitosa 只在 Lanheses 站连接。五行线路 LINE:003195、004799、004800、004820、004821 的八个端点字段改接到新节点；线路数量不增加。发布的 `model_field_diff.csv` 给出每个新增母线和每个改接字段的前后值、经纬度及来源状态。

母线差异并非工作库全部变化：逐字段比较还发现 1,457 个 `max_i_ka`、15 个长度、6 个并联数及 Estoi 接入/容量字段变化。完整差异表同时保留状态和证据标签变更。上述改动没有回写历史月库；任何使用者均须选择明确变体，不能用 3,787 母线的工作库替代核心模板重算并声称与全部历史结果相同。

最小复现命令（解压核心包后，在包根目录执行）为：

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/python replay.py
```

重算程序禁止网络请求，从冻结输入库读取数据，比较电压极值、最大线路负载率和净进口；容差为 10⁻⁵（分别采用对应字段单位）。`--case-index 0` 至 `21` 可重算全部 22 个验证断面。本次 22 个基线回归与原存档在上述字段的差值均为 0，另外在提取出的发布包中执行离线单例复现成功。完整月库可直接解压查询；新增审计和消融的协议、脚本、逐案例结果与失败配置记录一并发布。
'''
before('# 4. Validation',release)
# Small sample caveat adjacent to actual coverage table and discussion.
s=s.replace('60/130 kV 结果不能解释为独立准确率。','60/130 kV 结果不能解释为独立准确率。130 kV 仅有 7 条模型线路，长度比 1.090 的样本量很小，仅供参考，不能据此判断该电压层整体精度。',1)
# Add operational and hotspot sections before temporal validation.
op=pd.read_csv(D/'operational_ablations/summary.csv');names={'BASELINE':'基线','Q_HALF':'无功限值 ×0.5','Q_150_PERCENT':'无功限值 ×1.5','PV_099':'PV 目标 0.99','PV_101':'PV 目标 1.01','NO_LOAD_COMPENSATION':'停用负荷补偿','NO_REACTORS':'停用 5 个电抗器','FIXED_NEUTRAL_TAPS':'旧模型固定中性档','NO_COMPENSATION_FIXED_TAPS':'停用补偿/电抗器且固定档位','RATIO_TAP_CONTROL':'有效 Ratio 分接器及调压','RATIO_TAP_FIXED_NEUTRAL':'有效 Ratio 分接器但固定中性档'}
ops='''### 4.3.1 Operational-Proxy Ablations

新增消融固定使用与原敏感性实验完全相同的 22 个时点，每月最大负荷及最大风光合计各一，执行 11 种配置、合计 242 个交流潮流案例。无功限值、PV 目标、负荷补偿、电抗器和分接控制分别扰动；其余注入、线路参数、外边界和求解器保持一致。PV 调整只作用于不与外部平衡源共址的发电机，以免同一母线出现互相冲突的电压目标。全部有效配置均在强制无功限值下收敛；初次设置全部 PV 目标导致的 44 个约束配置冲突保留于 `invalid_configuration_audit`，不算作电网不收敛，也不进入 242 个有效配置统计。

归档模板的 228 台变压器 `tap_changer_type` 为空。在 pandapower 3.5.2 中，只改变 `tap_pos` 的旧调压代理未改变变比，因此“关闭旧分接控制”产生近零变化不能证明物理调压无影响。额外启用 Ratio 类型再运行相同档位搜索，作为独立修正实验；该结果未回写旧案例。基准及固定中性档的可重复性与新的有效控制须分开解释。
'''
ops+=table('Table R3——运行代理消融；最大绝对变化取 22 个成对时点。',['配置','收敛/尝试','最低电压 Δ（p.u.）','最高线路负载 Δ（百分点）','最高变压器负载 Δ（百分点）'],[[names[row.variant],f'{row.converged}/{row.attempts}',f'{row.max_abs_delta_vm_pu_min:.5f}',f'{row.max_abs_delta_maximum_line_loading_percent:.3f}',f'{row.max_abs_delta_maximum_transformer_loading_percent:.3f}'] for row in op.itertuples()])
ops+='''
无功限值减半使最低电压最大改变 0.02845 p.u.；有效 Ratio 分接控制使最大变压器负载率改变 4.305 个百分点。此处为正常运行断面的局部消融，不能否定第 5.5 节故障状态下更强的无功敏感性，也不证明这些代理范围等于运营商控制能力。

![Figure R1](revision_v3/figures/figR2_operational_ablations.png)

**Figure R1——运行代理消融的成对响应。** 两面板分别表示相对同一时点归档基线的最低电压和最大线路负载率的最大绝对变化，采用 22 个时点与固定的扰动幅度。Table R3 给出全部配置及变压器结果；分接器启用是显式独立变体。

### 4.3.2 Persistence of Ranked Hotspots

对原 264 个扰动结果重新计算每行线路进入 Top-20 的频率。每时点 12 种配置（基线及 11 种扰动）分别排名；并列值按稳定线路 ID 排序。定义 f(i,t)=入选次数/该时点成功配置数，f≥0.8 为“该时点持续热点”，0<f<0.8 为“依赖假设的热点”；未入选为 0。跨时点总频率另报，避免把季节变化误当参数不稳定。

4,943 条线路中 74 条至少入选一次；其中 56 条在至少一个时点达到持续热点阈值，18 条从未达到。最高的跨实验入选率为 207/264=78.4%（LINE:004925 与 LINE:000799），因此不能声称存在跨全年所有时点均稳定的热点集合。Top-20 Jaccard 最低 0.429 仍是重要限制；全网 Spearman 较高不能替代热点成员的稳定性证据。完整逐线路、逐时点频率和原入选成员表随发布提供。

![Figure R2](revision_v3/figures/figR3_hotspot_persistence.png)

**Figure R2——热点线路在不同假设下的入选频率。** 从全部线路按持续热点时点数、总入选次数排序展示前 20 条；每格为一个时点的 12 个配置中进入 Top-20 的比例，未入选为零。该图的“持续”以固定时点为条件，不等于跨时间持续过载，也不说明这些分段是 20 条独立物理回路。
'''
before('## 4.4 External Temporal Agreement',ops)
held=pd.read_csv(D/'heldout_spatial_summary.csv');hn={'CAPACITY_GLOBAL':'容量比例','GEOGRAPHIC_KNN5':'地理距离 5 邻站','NETWORK_ROUTE_KNN5':'网络路径 5 邻站'}
htext='''### 4.5.1 Station-Held-Out Spatial Reconstruction Test

新增验证以整座变电站为留出单元。按固定 SHA-256 规则分成 5 折，某站在所有时点均只作测试，不以其负荷或季节峰值校准权重。394 个可匹配已装容量与活动母线的站，在原 22 个代表时点产生 8,541 个非负、非缺失观测对。每次只用其他折同一时点的观测功率和公开安装容量推断留出站负荷，比较全局容量比例、地理距离和重建网络最短路径三种分配。

邻站方法固定 k=5，权重为 1/max(d,0.1 km)；网络距离使用在运线路长度、变压器边赋 0.001 km，仅表示连接邻近性而非电气阻抗。预测为留出站容量乘以训练邻站加权功率/容量比。全部折、邻域规则与逐对预测公开，95% MAE 区间对变电站作 1,000 次簇自助重采样。
'''
htext+=table('Table R4——整站留出的空间分配结果。',['方法','站数/观测对','MAE（MW）及 95% CI','RMSE（MW）','WAPE'],[[hn[r.method],f'{r.stations}/{r.pairs}',f'{r.mae_mw:.3f} [{r.mae_station_bootstrap_ci_low:.3f}, {r.mae_station_bootstrap_ci_high:.3f}]',f'{r.rmse_mw:.3f}',f'{100*r.wape:.1f}%'] for r in held.itertuples()])
htext+='\n网络路径方法 MAE 比全局容量比例下降约 9.9%，但未优于地理距离基准，且区间重叠。该实验直接检验未观测节点的空间分配可恢复性及重建连接的邻近信息，证据强于全国聚合相关；结果同时说明仅靠公开位置、连接和容量仍不足以获得精确节点负荷。它不是对 PDIRT 残差分配本身的独立检验，也不是线路潮流或开关拓扑真值验证。'
before('## 4.6 Validation Scope',htext)
proxy='''### 4.6.1 Quantified Dependence on Spatial Proxies

逐一读取全部 31,492 个审计包，对直接观测负荷、PDIRT 分配残差和 `UNMAPPED_NATIONAL_RESIDUAL_PROXY` 分别统计。前两项是消费侧分解，分母为 REN Consumption；发电代理的分母为全国发电或对应能源类型发电，不能将供给侧代理与需求侧两项相加成一个百分比。月占比采用能量权重，即各 15 min MW 的和之比；抽水与电池充电另列在逐区间表，不混入消费占比。
'''
ld=pd.read_csv(D/'proxy_monthly_summary.csv');gg=pd.read_csv(D/'generation_proxy_monthly_by_energy.csv').pivot(index='month',columns='energy',values='energy_weighted_proxy_share')
proxy+=table('Table R5——月度功率来源占比，以区间能量加权（%）。',['月份','观测负荷/消费','PDIRT/消费','代理/全国发电','代理/电池发电','代理/其他火电'],[[r.month,f'{r.direct_share_of_consumption*100:.2f}',f'{r.pdirt_share_of_consumption*100:.2f}',f'{r.generation_proxy_share*100:.3f}',f'{gg.at[r.month,"Battery Injection"]*100:.2f}',f'{gg.at[r.month,"Other Thermal"]*100:.2f}'] for r in ld.itertuples()])
proxy+='''
其余水电、光伏、风电、天然气和生物质的代理功率仅为浮点舍入误差；波浪发电为零，比例未定义而非验证通过。全国发电代理月占比仅 0.010%–0.063%，但 2025 年 5 月电池发电代理占 90.58%，同月其他火电为 11.07%；2025 年 9、10 月电池类别分别为 14.31%、12.27%。因此低全国代理占比不保证小能源类别的空间可靠性。

预先固定“消费残差 ≥50%”及“能源类别代理 ≥10%”作为标记阈值，完整时间清单在 CSV 中保留。2026 年 3 月有 59 个时点的消费残差超过 50%，最高为 52.88%；全国总发电代理无时点达到 10%，但类别级高代理时段明显存在。地域统计使用明确纬度分带：北部 ≥40.5°N、中部 38.5°–40.5°N、南部 <38.5°N，不冒称行政区域。南部月残差占比为 29.35%–42.69%，北部 24.57%–30.46%，中部 24.68%–31.86%。71 个接收母线在至少一个月的本地消费中代理占比达到 50%；完整母线表提供 ID，避免仅报告全国平均。

所有非舍入的全国发电代理均注入 `BUS:OSM:way:131715746:400`，位于上述中部纬度带。这个位置只是模型接收点，不是未映射电厂真实分布的证据；附近线路热点需连同这一集中注入假设解释。逐月八能源类型表、逐区间功率、区域/母线份额和高代理时段均可复算。

![Figure R3](revision_v3/figures/figR1_proxy_provenance.png)

**Figure R3——观测与代理功率的时间和空间分布。** (a) 消费侧观测/PDIRT 残差分解；(b) 电池和其他火电各自的发电代理占比，使用各自能源发电量作分母；(c) 模型接收母线按纬度分带后的负荷残差占比。三面板不得跨分母求和，区域是模型分配位置而非缺失资产真实位置。Table R5 和逐区间 CSV 保留数值。
'''
before('# 5. Application Example and Interactive Visualization',proxy)
old='每个时点均从归档输入重新构建交流潮流模型，并对 1,649 个具有基准电流、可恢复物理身份的带电元件进行逐一停运。没有基准电流的未带电地图支路不进入故障集。'
new='每个时点使用归档 N1-3787 修正基准，对同一组 1,649 个可求解候选元件逐一停运。故障集合的精确成员 SHA-256 在六个时点均为 `d9edd18f63382b16f967a090661b35fbc462e17fea565631ba3554724af44ce8`，不只是案例数量相同。这里的“全元件”严格指下述规则得到的可建模集合，不表示完整运营商物理故障清单。'
assert old in s;s=s.replace(old,new)
univ=table('Table R6——六个代表时点共同的 N−1 故障集合与排除规则。',['层次','数量','规则'],[['原模型线路分段','4,943','保留几何串联分段，不能按行等同物理回路'],['停运线路排除','156','in_service=False'],['活动站内母排排除','2','Rio Maior 两条 OSM 母排不是独立输电回路'],['活动非母排且无有效基准结果','14','缺失/NaN 负载结果；并非数值为零'],['入选线路模型分段','4,771','在运、非母排且结果有限；本面板恰有 0 行严格零电流'],['线路故障组','1,421','1,356 来源回路 ID；58 仅 OSM way 分段；7 其他身份'],['含并行等值的线路故障组','282','单回退出：parallel 减 1；串联分段按同组操作'],['变压器故障等值组','228','0 停运、0 缺失结果；并联组 1 个'],['变压器单位数（等值）','230','Estoi 3 台以 1 个对称故障代表，其他 227 台各 1'],['每时点故障数','1,649','1,421 + 228；不将对称并联单位重复加权']])
univ+='\n“无基准电流”应理解为无有效求解结果，不能与“零电流”或“未带电”画等号：零值是合法状态；是否带电还需在运标记、端点状态、供电连通性和解状态共同判断。归档筛选器实际按非缺失结果筛选，14 行的具体 ID 和端点状态见 `nminus1_exclusions.csv`。58 个 OSM way 组及 7 个其他身份组仍缺少运营商完整回路确认，因此不用“全部物理回路身份已恢复”的措辞。串联分段同时退出；等值并行元件每次仅减一回/一台。六个时点使用相同成员，停运操作并不意味着整个等值并行组退出。'
after('## 5.6 Full-Element Annual N−1 Panel',univ)
wor='''### 5.6.1 Incremental Criterion and Worsening of Existing Violations

令 L(c,t) 为物理回路 c 的各在运分段最大负载率，门限 τ(c) 对 60 kV 为 110%，其余为 100%。基准违规集 O₀(t)={c:L₀(c,t)>τ(c)}，故障 k 的新增热违规数为 |Oₖ(t)\\O₀(t)|。原增量通过还要求主潮流收敛、无实质孤岛；当基准电压全部在 0.90–1.10 p.u. 内时，故障电压亦须在范围内；当基准最大变压器负载 ≤120% 时，故障值亦须 ≤120%。若基准电压或变压器已经违规，原相应布尔指标不会识别恶化。六个时点的基准电压及变压器值均合格，因此本面板的实际盲点是既有线路热越限。

对主解收敛案例，新增指标为 W(k,t)=max{max[0,Lₖ(c,t)−L₀(c,t)]:c∈O₀(t)}，单位为负载率百分点；不存在原违规回路时定义为 0，主解失败为 NA。这是同一物理回路的成对变化，不是全网最大值相减。被停运或故障后下降到 100% 以下的原违规回路不会产生正恶化；归档超过 100% 的逐回路结果足以精确恢复正 W。另输出电压/变压器的极值越限幅度，但它们仅为全网包络，不能冒充逐设备恶化统计。
'''
w=pd.read_csv(D/'nminus1_worsening_summary.csv');wn={'MAX_NET_EXPORT':'最大净出口','MAX_NET_IMPORT':'最大净进口','MAX_SOLAR':'最大光伏','MAX_WIND':'最大风电','MINIMUM_LOAD':'最低负荷','PEAK_LOAD':'最大负荷'}
wor+=table('Table R7——已有线路违规的恶化；百分点阈值为研究诊断容差。',['时点','原违规回路','W>1 pp 案例','最大 W（pp）','旧增量通过但 W>1 pp','增量通过且 W≤1 pp'],[[wn[r.role],r.base_screen_overloaded_circuits,r.worsening_gt_1pp,f'{r.max_worsening_pp:.3f}',r.legacy_pass_but_worsening_gt1pp,r.incremental_and_no1pp_worsening] for r in w.itertuples()])
wor+='\n最大风电断面原有 3 条违规回路，故障后的最大恶化达 114.924 个百分点；23 个主解案例恶化超过 1 个百分点，其中 17 个曾被旧增量指标判为通过。最大负荷断面对应 1 条、15.023 个百分点、20 个案例和 15 个旧通过案例。加上 W≤1 pp 条件后，辅助通过数由 9,064 降为 9,032；阈值 0.1/1/5 pp 的完整计数随 CSV 报告。该辅助指标仍不替代绝对通过数 6,048，更不代表在既有过载下安全运行。'
before('## 5.7 Interpretation and Scope',wor)
s=s.replace('该指标用于定位故障引起的增量变化，不替代绝对安全判据。','该历史指标只识别新出现的违规，不能排除已有违规恶化；第 5.6.1 节给出判定公式和新增恶化统计，不替代绝对安全判据。')
s=s.replace('变压器 120% 短时阈值','变压器 120% 短时阈值')
# Conclusions must explicitly acknowledge new evidence and corrected control limitation.
before('# References','新增整站留出与运行代理消融进一步限定了结论：连接邻近性支持一定程度的空间预测，但未胜过地理距离基准；热点成员会随假设变化；旧模板的分接器实现不产生有效变比控制；已有越限会被单纯“无新增违规”判据漏掉。发布保留这些限制、完整结果和历史模型，后续模型改进必须使用新变体和新的校验记录。')
refs=[('APA3403','APA (2021). [Sobreequipamento do Parque Eólico de Trancoso: Parecer da Comissão de Avaliação, AIA 3403](https://siaia.apambiente.pt/AIADOC/AIA3403/parecerca_3403202192134944.pdf). July; PDF p. 5.'),('APAPPA421','APA (n.d.-a). [PPA 421: Sub-Parque Eólico de Sernancelhe e ligação a Moimenta](https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/421). AIA 2009; project-name and operation-date fields; archived 2026-09-21.'),('APAPPA407','APA (n.d.-b). [PPA 407: Ligação do Douro Sul à Subestação de Armamar e Subestação de Moimenta](https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/407). AIA 2009; archived 2026-09-21.'),('DGEGCasal2025','DGEG (2025). [Visita técnica a unidade de armazenamento de Casal da Cortiça, 18 de junho](https://www.dgeg.gov.pt/pt/areas-setoriais/energia/energia-eletrica/atividades-eventos/). Project announcement; archived 2026-09-21.'),('REE2012','Red Eléctrica de España (2012). [Interconexiones eléctricas: un paso para el mercado único de la energía en Europa](https://www.ree.es/sites/default/files/jgk4byy3ukct.pdf). September; PDF p. 10.'),('Tocha2019','EDP Distribuição (2019). [Memória Descritiva e Justificativa: Linha a 60 kV PE Tocha II–Tocha](https://siaia.apambiente.pt/AIADOC/AIA3274/projeto%20linha%20eletrica%20pe%20tocha%20ii2019729153214.pdf). 19 February; process 2800-19C007374; PDF pp. 3, 5.'),('REN2015Estoi','REN (2015). [PDIRT 2016–2025, Anexo 6: Equipamento em serviço previsto em finais de 2016, 2018, 2020 e 2025](https://www.erse.pt/media/b1edmm30/proposta_pdirt_e_2015_anexos.pdf). Proposal archive; PDF pp. 50, 52, 54, 56. Year follows archive label, not a newly inferred issue date.')]
for key,txt in refs:s+='\n\n<a id="ref-'+key+'"></a>\n\n'+txt+'\n'
s=s.replace('*Computers, Environment and Urban Systems*, 130, 102495.','*Computers, Environment and Urban Systems*, 130, 102495. Author/year/DOI verified against the official GeoPandas CITATION.md and Crossref on 2026-09-21; assigned issue December 2026, not asserted as the online publication date.',1)
# Renumber all main tables/figures by appearance; references are updated together.
for kind in ['Table','Figure']:
 ids=re.findall(r'\*\*'+kind+r' (R\d+|\d+)——',s);assert len(ids)==len(set(ids)),(kind,ids)
 mapping={old:str(i+1) for i,old in enumerate(ids)}
 s=re.sub(r'\b'+kind+r' (R\d+|\d+)(?!\d)',lambda m:kind+' '+mapping.get(m.group(1),m.group(1)),s)
 (D/(kind.lower()+'_renumbering.json')).write_text(json.dumps(mapping,indent=2))
assert '[CITATION NEEDED]' not in s
(P/'paper_final_edited.md').write_text(s)
print('Updated',len(s),'characters; tables',len(re.findall(r'\*\*Table \d+——',s)),'figures',len(re.findall(r'\*\*Figure \d+——',s)))
