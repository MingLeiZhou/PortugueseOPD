#!/usr/bin/env python3
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[2]; P=ROOT/'paper'
main=(P/'SimPT60_lean.md').read_text(); supp=(P/'SimPT60_supplement_lean.md').read_text(); body=main.split('# References',1)[0]
checks=[]
def check(name,ok,detail=None): checks.append({'check':name,'passed':bool(ok),'detail':detail})
def prose(text,label):
 for line in text.splitlines():
  if re.search(rf'(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])',line) and not line.strip().startswith(('![',f'**{label}——')): return True
 return False
check('One References section',len(re.findall(r'(?m)^# References$',main))==1)
check('No unresolved markers','[CITATION NEEDED]' not in main+supp)
for kind,n in [('Figure',10),('Table',12)]:
 caps=list(map(int,re.findall(r'\*\*'+kind+r' (\d+)——',body)))
 check(kind+' captions sequential',caps==list(range(1,n+1)),caps)
 missing=[i for i in range(1,n+1) if not prose(body,f'{kind} {i}')]
 check(kind+' prose references',not missing,missing)
imgs=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',body)
check('Ten main figures',len(imgs)==10,imgs)
for x in imgs: check('Main image exists: '+x,(P/x).is_file())
st=list(map(int,re.findall(r'\*\*Table S(\d+)——',supp))); sf=list(map(int,re.findall(r'\*\*Figure S(\d+)——',supp)))
check('Supplement tables S1-S9',st==list(range(1,10)),st)
check('Supplement figures S1-S2',sf==[1,2],sf)
for x in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',supp): check('Supplement image exists: '+x,(P/x).is_file())
anchors=set(re.findall(r'<a id="([^"]+)"></a>',supp)); links=set(re.findall(r'SimPT60_supplement_lean\.md#([^)]+)',body))
check('Supplement links resolve',links<=anchors,sorted(links-anchors))
refs=set(re.findall(r'<a id="ref-([^"]+)">',main)); cited=set(re.findall(r'\]\(#ref-([^)]+)\)',body+'\n'+supp))
check('Citations and bibliography agree',refs==cited,{'refs':len(refs),'cited':len(cited),'uncited':sorted(refs-cited),'missing':sorted(cited-refs)})
check('No duplicate main N-1 figure in supplement','fig12_annual_nminus1' not in supp and 'fig11_annual_nminus1' not in supp)
check('No duplicate main N-1 table in supplement','**Table S10——' not in supp)
for v in ['31,492','3,783','4,943','228','0.997','0.9998','9,894','9,888','6,048','9,064']:
 check('Core value retained: '+v,v in body)
report={'passed':sum(x['passed'] for x in checks),'total':len(checks),'checks':checks}
(P/'lean_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':report['passed'],'total':report['total'],'failures':[x for x in checks if not x['passed']]},ensure_ascii=False,indent=2))
assert all(x['passed'] for x in checks)
