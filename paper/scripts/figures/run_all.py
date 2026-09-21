#!/usr/bin/env python3
"""Rebuild the final eleven figures exclusively from the locked snapshot.

  .venv/bin/python paper/scripts/figures/run_all.py [--only 2 6]
"""
import argparse
import importlib
import json
from data import OUT,STEMS,verify_all
from style import setup

def main():
    p=argparse.ArgumentParser();p.add_argument('--only',type=int,nargs='+');a=p.parse_args()
    verify_all()  # Stop the complete build before overwriting any figure if sources drift.
    setup()
    for n in a.only or range(1,12):importlib.import_module(f'fig{n:02d}_{STEMS[n]}').build()
    manifests=[json.loads((OUT/f'fig{n:02d}_{s}.json').read_text()) for n,s in STEMS.items() if (OUT/f'fig{n:02d}_{s}.json').exists()]
    (OUT/'manifest.json').write_text(json.dumps(manifests,indent=2)+'\n')

if __name__=='__main__':main()
