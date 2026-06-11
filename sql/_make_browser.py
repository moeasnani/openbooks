import json,sys
nodes=json.load(open('mart/entity_nodes_vfirst.json'))
for n in nodes:
    n['transactions']=[{'fy':t['fy'],'agency':t.get('agency',''),'cat':t.get('cat',''),'amount':t['amount'],'score':t['score'],'tier':t['tier'],'markers':t.get('markers','')} for t in (n.get('transactions') or [])]
    n['top_markers']=n.get('top_markers') or []; n['agencies']=n.get('agencies') or []; n['names_merged']=n.get('names_merged',1)
payload=json.dumps(nodes,separators=(',',':'))
HTML=r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arizona High-Value Tiering — vendors</title>
<style>
:root{--bg:#0f1216;--card:#161b22;--card2:#1b212a;--line:#2a313b;--ink:#e6eaef;--mut:#8b97a7;--acc:#4ea1ff;--t1:#ff5d5d;--t2:#ffb020}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:9;background:#0f1216f2;backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:13px 18px}
h1{margin:0 0 3px;font-size:16px;font-weight:650}.sub{color:var(--mut);font-size:12px;margin-bottom:10px}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=search]{flex:1 1 240px;min-width:170px;background:var(--card);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:8px 11px;font-size:13px}
.lbl{color:var(--mut);font-size:11px;margin-right:2px}
.seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{background:var(--card);border:0;color:var(--mut);padding:8px 11px;font-size:12px;cursor:pointer}
.seg button.on{background:var(--acc);color:#06101f;font-weight:650}
.wrap{max-width:1080px;margin:0 auto;padding:8px 12px 70px}.count{color:var(--mut);font-size:12px;margin:9px 4px}
.grp{margin:6px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--card)}
.ghead{display:grid;grid-template-columns:16px 1fr auto;gap:10px;align-items:center;padding:11px 14px;cursor:pointer}
.ghead:hover{background:var(--card2)}.gname{font-weight:650;font-size:14px}.gmeta{color:var(--mut);font-size:11.5px;margin-top:2px}
.chev{color:var(--mut);transition:transform .14s;font-size:12px}.open>.ghead .chev,.open>.vhead .chev{transform:rotate(90deg)}
.gbody{display:none;padding:2px 8px 8px}.grp.open>.gbody{display:block}.list{margin:6px 0}
.v{border:1px solid var(--line);border-radius:10px;margin:6px 0;background:var(--card)}
.gbody .v{border:0;border-top:1px solid #232a33;border-radius:0;margin:0;background:transparent}.gbody .v:first-child{border-top:0}
.vhead{display:grid;grid-template-columns:16px 1fr auto;gap:10px;align-items:center;padding:10px 12px;cursor:pointer}
.vhead:hover{background:var(--card2)}.vname{font-weight:600;font-size:14px}
.vmeta{color:var(--mut);font-size:11px;margin-top:3px;display:flex;gap:9px;flex-wrap:wrap;align-items:center}
.right{text-align:right;white-space:nowrap}.exp{font-variant-numeric:tabular-nums;font-weight:650;font-size:14px}
.badge{display:inline-block;padding:0 6px;border-radius:20px;font-size:10px;font-weight:800;color:#0b0e12}.t1{background:var(--t1)}.t2{background:var(--t2)}
.chip{display:inline-block;padding:0 6px;border-radius:5px;font-size:10px;border:1px solid var(--line);color:var(--mut)}
.agchip{color:var(--acc);border-color:#264a72}.mgchip{color:#c4a6ff;border-color:#4a3a72}
.v-{font-size:10px;font-weight:700;padding:0 6px;border-radius:5px}
.v-genuine_review{background:#3a1414;color:#ff8e8e}.v-mixed{background:#3a2c10;color:#ffce82}
.v-explained_benign{background:#0f2a22;color:#6ee0b6}.v-false_positive_marker{background:#222831;color:#aab4c2}
.vbody{display:none;padding:2px 8px 11px 30px}.v.open>.vbody{display:block}
.ctx{color:var(--mut);font-size:12px;font-style:italic;margin:5px 0 9px}
table{width:100%;border-collapse:collapse;font-size:11.5px}th,td{text-align:left;padding:4px 7px;border-bottom:1px solid #20262e}
th{color:var(--mut)}td.n{text-align:right;font-variant-numeric:tabular-nums}.sc{font-weight:700}.mk{color:var(--mut);font-size:10.5px}
mark{background:#3a3410;color:#ffe28a;border-radius:2px}
</style></head><body>
<header><h1>Arizona high-value tiering &mdash; vendors</h1>
<div class="sub">One row per vendor (legal-name variants &amp; vendor-IDs merged to parent). Expand a vendor to see its transactions. Leads warranting confirmation, never findings.</div>
<div class="controls">
<input id="q" type="search" placeholder="Filter (optional): vendor, agency, marker&hellip;">
<span class="lbl">group:</span>
<div class="seg" id="grpseg">
<button data-g="none" class="on">Vendor</button><button data-g="primary_agency">Agency</button><button data-g="primary_cabinet">Cabinet</button><button data-g="top_tier">Tier</button><button data-g="verdict">Verdict</button></div>
</div></header>
<div class="wrap"><div class="count" id="count"></div><div id="tree"></div></div>
<script>
const NODES=__PAYLOAD__;
NODES.forEach(n=>n._h=(n.entity_name+' '+(n.agencies||[]).join(' ')+' '+n.primary_cabinet+' '+(n.top_markers||[]).join(' ')+' '+(n.public_context||'')).toLowerCase());
let GB='none', Q='';
const fmt=n=>n>=1e9?'$'+(n/1e9).toFixed(2)+'B':n>=1e6?'$'+(n/1e6).toFixed(1)+'M':n>=1e3?'$'+(n/1e3).toFixed(0)+'K':'$'+n;
const el=id=>document.getElementById(id);
const esc=s=>(''+s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const hl=s=>{s=esc(s);if(!Q)return s;return s.replace(new RegExp('('+Q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>')};
const gkey=n=>GB==='top_tier'?'Tier '+n.top_tier:GB==='verdict'?(n.verdict||'unreviewed').replace(/_/g,' '):n[GB];
function vrow(n){
 const agc=n.n_agencies>1?'<span class="chip agchip">'+n.n_agencies+' agencies</span>':'<span class="chip agchip">'+hl(n.primary_agency||'')+'</span>';
 const ids=n.n_ids>1?'<span class=chip>&#9733; '+n.n_ids+' IDs</span>':'';
 const mg=n.names_merged>1?'<span class="chip mgchip">&#9878; '+n.names_merged+' names</span>':'';
 const v=n.verdict?'<span class="v- v-'+n.verdict+'">'+n.verdict.replace(/_/g,' ')+(n.overtaker_interest?' '+n.overtaker_interest+'/5':'')+'</span>':'';
 const mk=(n.top_markers||[]).slice(0,4).map(m=>'<span class=chip>'+m+'</span>').join('');
 const ctx=n.public_context?'<div class=ctx>'+hl(n.public_context)+'</div>':'';
 const tx=(n.transactions||[]).map(t=>'<tr><td>'+t.fy+'</td><td>'+esc(t.agency||'')+'</td><td class=n>'+fmt(t.amount)+'</td><td class="n sc">'+t.score+'</td><td>T'+t.tier+'</td><td class=mk>'+esc((t.markers||'').replace(/\|/g,', '))+'</td></tr>').join('');
 const more=n.n_txn>(n.transactions||[]).length?'<div class=mk style="padding:4px 7px">+ '+(n.n_txn-(n.transactions||[]).length)+' more transactions</div>':'';
 return '<div class="v"><div class=vhead><span class=chev>&#9656;</span>'+
  '<div><div class=vname>'+hl(n.entity_name)+' <span class="badge t'+n.top_tier+'">T'+n.top_tier+'</span></div>'+
  '<div class=vmeta>'+agc+'<span>'+n.n_tier1+' T1&middot;'+n.n_tier2+' T2 / '+n.n_txn+' txns</span><span>FY'+n.fy0+'&ndash;'+n.fy1+'</span>'+ids+mg+v+mk+'</div></div>'+
  '<div class=right><div class=exp>'+fmt(n.usd_tier1||n.exposure)+'</div></div></div>'+
  '<div class=vbody>'+ctx+'<table><thead><tr><th>FY</th><th>Agency</th><th class=n>Amount</th><th class=n>Score</th><th>Tier</th><th>Markers</th></tr></thead><tbody>'+tx+'</tbody></table>'+more+'</div></div>';
}
function render(){
 const rows=Q?NODES.filter(n=>n._h.includes(Q)):NODES;
 const tot=rows.reduce((s,n)=>s+(n.usd_tier1||n.exposure),0);
 if(GB==='none'){
  rows.sort((a,b)=>(b.usd_tier1||b.exposure)-(a.usd_tier1||a.exposure));
  el('count').innerHTML=rows.length+' vendors &middot; '+rows.filter(n=>n.top_tier===1).length+' with Tier-1 &middot; '+fmt(tot)+(Q?' &middot; filtered':'');
  const cap=Q?rows.length:Math.min(rows.length,500);
  el('tree').innerHTML='<div class=list>'+rows.slice(0,cap).map(vrow).join('')+'</div>'+(rows.length>cap?'<div class=count>showing top '+cap+' of '+rows.length+' &mdash; filter to narrow</div>':'');
  return;
 }
 const groups={};rows.forEach(n=>{const k=gkey(n);(groups[k]=groups[k]||[]).push(n);});
 const keys=Object.keys(groups).sort((a,b)=>groups[b].reduce((s,n)=>s+n.exposure,0)-groups[a].reduce((s,n)=>s+n.exposure,0));
 el('count').innerHTML=rows.length+' vendors in '+keys.length+' groups &middot; '+fmt(tot)+(Q?' &middot; filtered':'');
 const open=!!Q;
 el('tree').innerHTML=keys.map(k=>{
  const g=groups[k].sort((a,b)=>(b.usd_tier1||b.exposure)-(a.usd_tier1||a.exposure));
  const t1=g.filter(n=>n.top_tier===1).length, ex=g.reduce((s,n)=>s+(n.usd_tier1||n.exposure),0);
  return '<div class="grp'+(open?' open':'')+'"><div class=ghead><span class=chev>&#9656;</span>'+
   '<div><div class=gname>'+hl(k)+'</div><div class=gmeta>'+g.length+' vendors &middot; '+t1+' Tier-1 &middot; '+fmt(ex)+'</div></div>'+
   '<div class=right><div class=exp>'+fmt(ex)+'</div></div></div><div class=gbody>'+(open?g.map(vrow).join(''):'')+'</div></div>';
 }).join('');
}
el('tree').addEventListener('click',e=>{
 const gh=e.target.closest('.ghead');
 if(gh){const grp=gh.parentNode,body=grp.querySelector('.gbody');
  if(!grp.classList.contains('open') && !body.dataset.r){
   const k=gh.querySelector('.gname').textContent;
   const g=(Q?NODES.filter(n=>n._h.includes(Q)):NODES).filter(n=>gkey(n)===k).sort((a,b)=>(b.usd_tier1||b.exposure)-(a.usd_tier1||a.exposure));
   body.innerHTML=g.map(vrow).join('');body.dataset.r='1';}
  grp.classList.toggle('open');return;}
 const vh=e.target.closest('.vhead');if(vh){vh.parentNode.classList.toggle('open');}
});
el('q').addEventListener('input',e=>{Q=e.target.value.trim().toLowerCase();render();});
el('grpseg').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 [...e.currentTarget.children].forEach(x=>x.classList.toggle('on',x===b));GB=b.dataset.g;render();});
render();
</script></body></html>'''
open('overtaker_handoff/feed/tier_browser.html','w').write(HTML.replace('__PAYLOAD__',payload))
print('tier_browser.html rebuilt:',len(nodes),'vendors,',sum(1 for n in nodes if n['names_merged']>1),'with merged names')
