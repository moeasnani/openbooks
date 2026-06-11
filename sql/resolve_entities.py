#!/usr/bin/env python3
"""Conservative, auditable parent-company resolution over high-value vendor names.
Rules (deterministic, no black-box fuzzy score):
  - normalize: upper, punctuation->space, & -> AND, drop legal suffixes & stopwords
  - JOINT VENTURES are protected: a JV only merges with an identical-core JV (never into a partner)
  - merge non-JV pairs that share their first significant token AND one of:
      exact   : same core token set                       (confidence high)
      subset  : one core set fully contains the other,
                smaller has >=2 significant tokens         (confidence high)
      jaccard : token Jaccard >= 0.67 AND first 2 tokens   (confidence medium)
  - parent = highest-exposure member; crosswalk + audit report emitted.
"""
import csv, re, sys
from collections import defaultdict

SUFFIX = {'INC','INCORPORATED','LLC','LLP','LP','LLLP','LLLC','CORP','CORPORATION','CO','COMPANY',
          'LTD','LIMITED','PC','PLLC','PLC','APC','PA','PLLP','USA','NA','NATL','THE','AN','GROUP'}
STOP = {'A','AND','OF','THE','FOR','TO'}
JV_RE = re.compile(r'\b(JOINT VENTURE|JT VENTURE|JV|J V|A JV)\b')
# government/public umbrella names: distinct entities share a prefix (MARICOPA COUNTY ...),
# so subset-merge would wrongly fuse them. They're outside vendor scrutiny anyway -> never merge.
GOVT_RE = re.compile(r'\b(COUNTY|CITY OF|TOWN OF|STATE OF|UNIVERSITY|COMMUNITY COLLEGE|COLLEGE DISTRICT|'
                     r'SCHOOL DISTRICT|UNIFIED SCHOOL|HIGH SCHOOL|TREASURER|DEPARTMENT OF|DEPT OF|'
                     r'RETIREMENT SYSTEM|REGENTS|MUNICIPAL|CONSERVATION DISTRICT|FIRE DISTRICT|FAIR ASSOCIATION)\b')

def norm_tokens(name):
    s = name.upper().replace('&',' AND ')
    s = re.sub(r"[.,/()\-'\"#]", ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    toks = [t for t in s.split(' ') if t]
    core = [t for t in toks if t not in SUFFIX and t not in STOP and len(t) >= 2]
    return core

def load(path):
    rows = []
    for r in csv.DictReader(open(path)):
        r['exposure'] = float(r['exposure'] or 0); r['n_txn'] = int(r['n_txn'] or 0)
        r['n_flagged'] = int(r['n_flagged'] or 0); r['has_tier1'] = int(r['has_tier1'] or 0)
        r['core'] = norm_tokens(r['entity_key'])
        r['cset'] = frozenset(r['core'])
        r['jv'] = bool(JV_RE.search(' '.join(r['entity_key'].upper().split())))
        r['govt'] = bool(GOVT_RE.search(r['entity_key'].upper()))
        r['first'] = r['core'][0] if r['core'] else r['entity_key']
        rows.append(r)
    return rows

class UF:
    def __init__(s): s.p={}
    def find(s,x):
        s.p.setdefault(x,x)
        while s.p[x]!=x: s.p[x]=s.p[s.p[x]]; x=s.p[x]
        return x
    def union(s,a,b):
        ra,rb=s.find(a),s.find(b)
        if ra!=rb: s.p[ra]=rb

def jaccard(a,b):
    if not a or not b: return 0.0
    return len(a&b)/len(a|b)

def resolve(rows):
    uf=UF(); reason={}
    for r in rows: uf.find(r['entity_key'])
    blocks=defaultdict(list)
    for r in rows: blocks[r['first']].append(r)
    for first, members in blocks.items():
        if len(members)<2: continue
        for i in range(len(members)):
            for j in range(i+1,len(members)):
                A,B=members[i],members[j]
                if A['govt'] or B['govt']:      # never merge government/public umbrella names
                    continue
                if A['jv']!=B['jv']:            # never merge JV with non-JV
                    continue
                a,b=A['cset'],B['cset']
                if not a or not b: continue
                if A['jv'] and B['jv']:
                    if a==b: m=('norm_exact','high')
                    else: continue             # JV only merges with identical-core JV
                elif a==b:
                    m=('norm_exact','high')
                elif (a<b and len(a)>=2) or (b<a and len(b)>=2):
                    m=('token_subset','high')
                elif jaccard(a,b)>=0.67 and sorted(a)[:2]==sorted(b)[:2]:
                    m=('token_jaccard','medium')
                else:
                    continue
                uf.union(A['entity_key'],B['entity_key'])
                reason[frozenset((A['entity_key'],B['entity_key']))]=m
    # group + choose parent (max exposure)
    groups=defaultdict(list)
    for r in rows: groups[uf.find(r['entity_key'])].append(r)
    cross={}; merges=[]
    for root,members in groups.items():
        parent=max(members,key=lambda r:(r['exposure'],r['n_txn']))
        # group method/confidence = strongest edge present
        ms=[reason[k] for k in reason if all(any(e==x['entity_key'] for x in members) for e in k)]
        conf='high' if any(c=='high' for _,c in ms) else ('medium' if ms else 'singleton')
        meth='+'.join(sorted({mm for mm,_ in ms})) or 'singleton'
        for r in members:
            cross[r['entity_key']]=dict(parent_key=parent['entity_key'], parent_name=parent['display_name'],
                                        method=meth if r['entity_key']!=parent['entity_key'] else 'parent',
                                        confidence=conf, group_size=len(members))
        if len(members)>1:
            merges.append((parent, sorted(members,key=lambda r:-r['exposure']), meth, conf))
    return cross, merges

def main():
    rows=load('mart/entity_keys.csv')
    cross,merges=resolve(rows)
    with open('mart/entity_crosswalk.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['entity_key','parent_key','parent_name','method','confidence','group_size'])
        for k,v in sorted(cross.items()): w.writerow([k,v['parent_key'],v['parent_name'],v['method'],v['confidence'],v['group_size']])
    merges.sort(key=lambda m:-sum(r['exposure'] for r in m[1]))
    with open('mart/entity_merges_report.txt','w') as f:
        f.write(f"{len(merges)} multi-name parent groups merged from {len(rows)} names\n\n")
        for parent,members,meth,conf in merges:
            f.write(f"=> {parent['display_name']}  [{meth}/{conf}]  ${sum(r['exposure'] for r in members)/1e6:.1f}M\n")
            for r in members:
                star='*' if r['has_tier1'] else (' ' if r['n_flagged'] else '.')
                f.write(f"     {star} {r['display_name']:42}  ${r['exposure']/1e6:7.1f}M  {r['n_txn']} txns\n")
            f.write("\n")
    nm=len(merges); nnames=sum(len(m[1]) for m in merges)
    flagged_merges=[m for m in merges if any(r['n_flagged'] for r in m[1])]
    print(f"resolved {len(rows)} names -> {len(set(v['parent_key'] for v in cross.values()))} parents")
    print(f"{nm} multi-name groups absorbing {nnames} names ({nnames-nm} names eliminated)")
    print(f"{len(flagged_merges)} groups touch a flagged vendor")
    print("\n--- merges involving a Tier-1 vendor (audit) ---")
    for parent,members,meth,conf in merges:
        if any(r['has_tier1'] for r in members):
            names=' + '.join(r['display_name'] for r in members)
            print(f"  [{conf}] {parent['display_name']}  <=  {names}")

if __name__=='__main__': main()
