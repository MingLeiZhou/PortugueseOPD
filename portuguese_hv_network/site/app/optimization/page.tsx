'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';

type Audit = { feasible: boolean; objective: number; seconds: number | null; max_p_balance_residual_mw: number;
  max_q_balance_residual_mvar: number; max_branch_s_violation_mva: number; voltage_mae_pu?: number; generator_p_mae_mw?: number; fallback_to_cold_start?: boolean };
type Experiment = { name: string; label: string; opf: Audit; graph: string; policy: string };
type Results = { cases: Experiment[]; roundtrip: { roundtrip_pass: boolean; objective_relative_difference: number };
  training?: { epochs: number; selected_epoch: number; graph_counts: Record<string, number>; training_seconds: number;
    test: Array<{ pretrained: Audit; finetuned: Audit }> }; repair?: Audit };
const numeric = (v: number | null | undefined) => v == null ? '—' : Math.abs(v) > 0 && Math.abs(v) < .001 ? v.toExponential(2) : v.toLocaleString('zh-CN', { maximumFractionDigits: 4 });

export default function Optimization() {
  const [data, setData] = useState<Results | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/data/experiments/index.json', { signal: controller.signal }).then((r) => {
      if (!r.ok) throw new Error('Unavailable');
      return r.json() as Promise<Results>;
    }).then(setData).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);
  const test = data?.training?.test[0];
  return <main className="experiment-page"><div className="experiment-content">
    <Link href="/">← 返回电网地图</Link>
    <p role="note">归档实验：本页保留既有结果，已退出 PT60 核心交付范围，后续扩展暂停。<Link href="/project">返回当前项目入口 →</Link></p>
    <header><p className="eyebrow">PT60 · 优化与学习实验</p><h1>从交流潮流案例到优化调度与预测</h1>
      <p>以 PT60 网络与工况为输入，定义可调机组、成本和运行边界，求解 AC-OPF，再训练和检验神经网络。这里展示实际运行的实验结果。</p></header>
    <section className="experiment-method"><h2>输入 → 方法 → 输出</h2><div className="experiment-flow">
      <article><b>01 · 输入</b><p>冻结网络、时点负荷、固定注入，以及可替换的成本与边界参数。</p></article>
      <article><b>02 · 优化与学习</b><p>AC-OPF 生成可行标签；GridSFM 进行预训练预测和 PT60 微调。不同原始时点用于训练、验证与测试。</p></article>
      <article><b>03 · 验证后的结果</b><p>调度、电压、目标值及功率平衡残差。预测是否可行，以独立物理检查为准。</p></article>
    </div><p>成本采用明确标注的基准假设，可按机组替换；结果不代表葡萄牙真实市场报价下的最优调度。固定 PQ 注入及非参考边界保持给定，储能不做跨时段优化。</p></section>
    {!data && <output>{error ? '实验数据未能载入，请刷新重试。' : '正在载入实验数据…'}</output>}
    {data && <>
      <section><h2>AC-OPF 与扰动场景</h2><p>对母线电压、机组 P/Q、两端视在功率和节点功率平衡逐项核查。支路约束使用视在功率，与地图上的电流负载率口径不同。</p>
        <div className="experiment-table"><table><thead><tr><th>案例</th><th>物理检查</th><th>基准目标值</th><th>最大 P 残差 / MW</th><th>求解 / 秒</th><th>下载</th></tr></thead>
          <tbody>{data.cases.map((r) => <tr key={r.name}><td>{r.label}</td><td>{r.opf.feasible ? '通过' : '未通过'}</td><td>{numeric(r.opf.objective)}</td><td>{numeric(r.opf.max_p_balance_residual_mw)}</td><td>{numeric(r.opf.seconds)}</td><td><a href={r.graph} download>图数据</a> · <a href={r.policy} download>参数</a></td></tr>)}</tbody></table></div>
        <p>图数据冷启动复算：{data.roundtrip.roundtrip_pass ? '通过' : '未通过'}；目标值相对差异 {numeric(data.roundtrip.objective_relative_difference)}。</p>
      </section>
      {test && <section><h2>神经网络：独立测试案例</h2>
        <p>使用 Microsoft GridSFM 公开权重作为起点。训练 {data.training?.epochs} 轮，仅按验证损失选择第 {(data.training?.selected_epoch ?? 0) + 1} 轮模型；测试集不参与选择。这是小样本功能验证，不是泛化能力结论。</p>
        <div className="experiment-table"><table><thead><tr><th>方法</th><th>电压 MAE / p.u.</th><th>机组 P MAE / MW</th><th>最大 P 残差 / MW</th><th>物理检查</th><th>推理 / 秒</th></tr></thead>
          <tbody>{[['预训练权重', test.pretrained], ['PT60 微调', test.finetuned]].map(([label, audit]) => {
            const r = audit as Audit;
            return <tr key={label as string}><td>{label as string}</td><td>{numeric(r.voltage_mae_pu)}</td><td>{numeric(r.generator_p_mae_mw)}</td><td>{numeric(r.max_p_balance_residual_mw)}</td><td>{r.feasible ? '通过' : '未通过，不能直接作为可行调度'}</td><td>{numeric(r.seconds)}</td></tr>;
          })}</tbody></table></div>
      </section>}
      {data.repair && <section><h2>预测后的优化校正</h2><p>将预测作为优化初值，保留原约束重新求解。{data.repair.fallback_to_cold_start && '本次预测初值未能收敛，已回退到冷启动；没有获得求解加速。'}基准案例的物理检查{data.repair.feasible ? '通过' : '未通过'}，全过程耗时 {numeric(data.repair.seconds)} 秒。该时间不计入上表的纯推理时间。</p></section>}
      <section><h2>如何复现</h2><pre>{`pt60 opf-policy --output policy.json
pt60 opf --model solved-case.json --policy policy.json --output work/opf
pt60 graph-export --opf work/opf --output work/case.pyg.json
pt60 graph-check --graph work/case.pyg.json --output work/roundtrip.json
pt60 predict --graph work/case.pyg.json --checkpoint gridsfm_open_v1.1.pt --output work/prediction
pt60 finetune --manifest splits.json --checkpoint gridsfm_open_v1.1.pt --output work/training`}</pre>
        <p>以上计算在本地 Python 环境执行。网页提供已计算结果，完整参数、划分与检查记录可下载。</p>
        <a href="/data/experiments/index.json" download>下载实验报告</a> · <a href="/data/experiments/guide.md" download>下载使用说明</a>
      </section>
    </>}
  </div></main>;
}
