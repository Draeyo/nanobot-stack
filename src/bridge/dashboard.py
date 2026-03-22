"""HTML dashboard endpoint for unified health/usage view.

Renders a self-contained HTML page with live data fetched from the
bridge's own diagnostic endpoints via JavaScript.
"""
from __future__ import annotations
import os

DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>nanobot stack dashboard</title>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--text:#e0e0e0;--muted:#888;--green:#22c55e;--red:#ef4444;--yellow:#eab308;--blue:#3b82f6}
*{margin:0;padding:0;box-sizing:border-box}body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem}
h1{font-size:1.5rem;margin-bottom:1rem;color:var(--blue)}h2{font-size:1rem;margin-bottom:.5rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1rem;margin-bottom:1.5rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:.5rem;padding:1rem}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:.25rem;font-size:.8rem;font-weight:600}
.ok{background:#16331f;color:var(--green)}.err{background:#3b1111;color:var(--red)}.warn{background:#332b00;color:var(--yellow)}
.mono{font-family:monospace;font-size:.85rem;white-space:pre-wrap;max-height:300px;overflow:auto;background:#12141d;padding:.5rem;border-radius:.25rem;margin-top:.5rem}
.stat{display:flex;justify-content:space-between;padding:.3rem 0;border-bottom:1px solid var(--border)}.stat:last-child{border:none}
.stat-val{font-weight:600;color:var(--blue)}
.refresh{float:right;background:var(--blue);color:#fff;border:none;padding:.4rem 1rem;border-radius:.25rem;cursor:pointer;font-size:.85rem}
#last-update{color:var(--muted);font-size:.8rem;margin-left:1rem}
</style></head><body>
<h1>nanobot stack <button class="refresh" onclick="refresh()">Refresh</button><span id="last-update"></span></h1>
<div class="grid">
<div class="card" id="health-card"><h2>Health</h2><div id="health">loading...</div></div>
<div class="card" id="cb-card"><h2>Circuit Breakers</h2><div id="cbs">loading...</div></div>
<div class="card" id="cache-card"><h2>Embedding Cache</h2><div id="cache">loading...</div></div>
<div class="card" id="rate-card"><h2>Rate Limits</h2><div id="rates">loading...</div></div>
<div class="card" id="feedback-card"><h2>RAG Feedback</h2><div id="feedback">loading...</div></div>
<div class="card" id="ingest-card"><h2>Ingestion</h2><div id="ingest">loading...</div></div>
<div class="card" id="profile-card"><h2>User Profile</h2><div id="profile">loading...</div></div>
</div>
<div class="card"><h2>Routes</h2><div id="routes" class="mono">loading...</div></div>
<script>
const T=document.getElementById.bind(document),H=(s)=>s?'<span class="badge ok">OK</span>':'<span class="badge err">FAIL</span>';
function stat(k,v){return`<div class="stat"><span>${k}</span><span class="stat-val">${v}</span></div>`}
async function fe(p){const r=await fetch(p,{headers:{'X-Bridge-Token':localStorage.getItem('bt')||''}});return r.json()}
async function refresh(){
try{
const h=await fe('/healthz');
T('health').innerHTML=stat('Status',H(h.ok))+stat('Qdrant',H(h.qdrant?.ok))+stat('Collections',h.qdrant?.collections||'?')+
stat('API Keys',H(h.api_keys?.configured))+stat('Reranker',h.reranker?.loaded?'loaded':'fallback')+stat('Langfuse',H(h.langfuse?.enabled));
}catch(e){T('health').innerHTML='<span class="badge err">unreachable</span>'}
try{const c=await fe('/circuit-breakers');T('cbs').innerHTML=(c.circuit_breakers||[]).map(b=>stat(b.name,`<span class="badge ${b.state==='closed'?'ok':b.state==='open'?'err':'warn'}">${b.state}</span> (${b.failure_count}/${b.failure_threshold})`)).join('')||'none'}catch(e){T('cbs').textContent=e}
try{const c=await fe('/cache-stats');const s=c.embedding_cache;T('cache').innerHTML=stat('Size',`${s.size}/${s.max_size}`)+stat('Hit rate',`${(s.hit_rate*100).toFixed(1)}%`)+stat('Hits',s.hits)+stat('Misses',s.misses)}catch(e){T('cache').textContent=e}
try{const r=await fe('/rate-limits');T('rates').innerHTML=Object.entries(r.rate_limits||{}).map(([k,v])=>stat(k,`${v.tokens_available.toFixed(0)}/${v.capacity}`)).join('')||'none'}catch(e){T('rates').textContent=e}
try{const f=await fe('/feedback-stats');T('feedback').innerHTML=stat('Signals',f.total_signals||0)+stat('Positive',f.positive||0)+stat('Negative',f.negative||0)+stat('Boosted chunks',f.chunks_with_boost||0)}catch(e){T('feedback').textContent=e}
try{const i=await fe('/ingest-status');T('ingest').innerHTML=stat('Running',i.running?'yes':'no')+stat('Last run',i.last_run||'never')+
(i.last_result?stat('Indexed',i.last_result.indexed?.length||0)+stat('Skipped',i.last_result.skipped||0)+stat('GC deleted',i.last_result.gc_deleted||0):'')
}catch(e){T('ingest').textContent=e}
try{const p=await fe('/profile');T('profile').innerHTML=Object.entries(p).filter(([k])=>k!=='updated_at').map(([k,v])=>stat(k,typeof v==='object'?JSON.stringify(v):v||'—')).join('')}catch(e){T('profile').textContent=e}
try{const r=await fe('/routes');T('routes').textContent=JSON.stringify(r.task_routes,null,2)}catch(e){T('routes').textContent=e}
T('last-update').textContent='updated '+new Date().toLocaleTimeString()
}
if(!localStorage.getItem('bt')){const t=prompt('Bridge token:');if(t)localStorage.setItem('bt',t)}
refresh();setInterval(refresh,30000);
</script></body></html>"""


def get_dashboard_html() -> str:
    return DASHBOARD_HTML
