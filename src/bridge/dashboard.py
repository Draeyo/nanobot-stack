"""HTML dashboard with interactive Chart.js graphs.

Renders a self-contained HTML page with live charts for:
- Token usage over time
- Cost breakdown by model
- Cache hit rates
- Circuit breaker states
- Knowledge graph stats
"""
from __future__ import annotations
import os

DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>nanobot stack dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--text:#e0e0e0;--muted:#888;--green:#22c55e;--red:#ef4444;--yellow:#eab308;--blue:#3b82f6;--purple:#8b5cf6;--cyan:#06b6d4}
*{margin:0;padding:0;box-sizing:border-box}body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem}
h1{font-size:1.5rem;margin-bottom:1rem;color:var(--blue)}h2{font-size:1rem;margin-bottom:.5rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1rem;margin-bottom:1.5rem}
.grid-wide{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:1rem;margin-bottom:1.5rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:.5rem;padding:1rem}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:.25rem;font-size:.8rem;font-weight:600}
.ok{background:#16331f;color:var(--green)}.err{background:#3b1111;color:var(--red)}.warn{background:#332b00;color:var(--yellow)}
.mono{font-family:monospace;font-size:.85rem;white-space:pre-wrap;max-height:300px;overflow:auto;background:#12141d;padding:.5rem;border-radius:.25rem;margin-top:.5rem}
.stat{display:flex;justify-content:space-between;padding:.3rem 0;border-bottom:1px solid var(--border)}.stat:last-child{border:none}
.stat-val{font-weight:600;color:var(--blue)}
.refresh{float:right;background:var(--blue);color:#fff;border:none;padding:.4rem 1rem;border-radius:.25rem;cursor:pointer;font-size:.85rem}
#last-update{color:var(--muted);font-size:.8rem;margin-left:1rem}
.chart-container{position:relative;height:220px;margin-top:.5rem}
</style></head><body>
<h1>nanobot stack v9 <button class="refresh" onclick="refresh()">Refresh</button><span id="last-update"></span></h1>

<div class="grid">
<div class="card"><h2>Health</h2><div id="health">loading...</div></div>
<div class="card"><h2>Circuit Breakers</h2><div id="cbs">loading...</div></div>
<div class="card"><h2>Caches</h2><div id="cache">loading...</div></div>
<div class="card"><h2>Rate Limits</h2><div id="rates">loading...</div></div>
<div class="card"><h2>RAG Feedback</h2><div id="feedback">loading...</div></div>
<div class="card"><h2>Ingestion</h2><div id="ingest">loading...</div></div>
<div class="card"><h2>User Profile</h2><div id="profile">loading...</div></div>
<div class="card"><h2>Knowledge Graph</h2><div id="kg">loading...</div></div>
</div>

<div class="grid-wide">
<div class="card"><h2>Token Usage by Model</h2><div class="chart-container"><canvas id="costChart"></canvas></div></div>
<div class="card"><h2>Cache Performance</h2><div class="chart-container"><canvas id="cacheChart"></canvas></div></div>
</div>

<div class="grid-wide">
<div class="card"><h2>Working Memory</h2><div id="wm">loading...</div></div>
<div class="card"><h2>Plugins</h2><div id="plugins">loading...</div></div>
</div>

<div class="card"><h2>Routes</h2><div id="routes" class="mono">loading...</div></div>

<script>
const T=document.getElementById.bind(document),H=(s)=>s?'<span class="badge ok">OK</span>':'<span class="badge err">FAIL</span>';
function stat(k,v){return`<div class="stat"><span>${k}</span><span class="stat-val">${v}</span></div>`}
async function fe(p){const r=await fetch(p,{headers:{'X-Bridge-Token':localStorage.getItem('bt')||''}});return r.json()}

let costChart=null, cacheChart=null;

function initCharts(){
  const ctx1=T('costChart').getContext('2d');
  costChart=new Chart(ctx1,{type:'bar',data:{labels:[],datasets:[{label:'Cost (USD)',data:[],backgroundColor:'rgba(59,130,246,0.6)',borderColor:'rgba(59,130,246,1)',borderWidth:1},{label:'Calls',data:[],backgroundColor:'rgba(139,92,246,0.4)',borderColor:'rgba(139,92,246,1)',borderWidth:1,yAxisID:'y1'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#888'}}},scales:{x:{ticks:{color:'#888',maxRotation:45}},y:{ticks:{color:'#888'},title:{display:true,text:'Cost ($)',color:'#888'}},y1:{position:'right',ticks:{color:'#888'},title:{display:true,text:'Calls',color:'#888'},grid:{drawOnChartArea:false}}}}});
  const ctx2=T('cacheChart').getContext('2d');
  cacheChart=new Chart(ctx2,{type:'doughnut',data:{labels:['Embed Hits','Embed Misses','LLM Hits','LLM Misses'],datasets:[{data:[0,0,0,0],backgroundColor:['#22c55e','#ef4444','#3b82f6','#eab308']}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#888'}}}}});
}

async function refresh(){
try{
const h=await fe('/healthz');
T('health').innerHTML=stat('Status',H(h.ok))+stat('Qdrant',H(h.qdrant?.ok))+stat('Collections',h.qdrant?.collections||'?')+
stat('API Keys',H(h.api_keys?.configured))+stat('Reranker',h.reranker?.loaded?'loaded':'fallback')+stat('Langfuse',H(h.langfuse?.enabled));
}catch(e){T('health').innerHTML='<span class="badge err">unreachable</span>'}

try{const c=await fe('/circuit-breakers');T('cbs').innerHTML=(c.circuit_breakers||[]).map(b=>stat(b.name,'<span class="badge '+(b.state==='closed'?'ok':b.state==='open'?'err':'warn')+'">'+b.state+'</span> ('+b.failure_count+'/'+b.failure_threshold+')')).join('')||'none'}catch(e){T('cbs').textContent=e}

try{
const c=await fe('/cache-stats');const s=c.embedding_cache;const l=c.llm_cache||{size:0,max_size:0,hit_rate:0,hits:0,misses:0};
T('cache').innerHTML=stat('Embed Size',s.size+'/'+s.max_size)+stat('Embed Hit Rate',(s.hit_rate*100).toFixed(1)+'%')+stat('LLM Size',l.size+'/'+l.max_size)+stat('LLM Hit Rate',(l.hit_rate*100).toFixed(1)+'%');
if(cacheChart){cacheChart.data.datasets[0].data=[s.hits,s.misses,l.hits||0,l.misses||0];cacheChart.update()}
}catch(e){T('cache').textContent=e}

try{const r=await fe('/rate-limits');T('rates').innerHTML=Object.entries(r.rate_limits||{}).map(function(e){return stat(e[0],e[1].tokens_available.toFixed(0)+'/'+e[1].capacity)}).join('')||'none'}catch(e){T('rates').textContent=e}
try{const f=await fe('/feedback-stats');T('feedback').innerHTML=stat('Signals',f.total_signals||0)+stat('Positive',f.positive||0)+stat('Negative',f.negative||0)+stat('Boosted',f.chunks_with_boost||0)}catch(e){T('feedback').textContent=e}
try{const i=await fe('/ingest-status');T('ingest').innerHTML=stat('Running',i.running?'yes':'no')+stat('Last run',i.last_run||'never')+
(i.last_result?stat('Indexed',i.last_result.indexed?.length||0)+stat('Skipped',i.last_result.skipped||0)+stat('GC',i.last_result.gc_deleted||0):'')
}catch(e){T('ingest').textContent=e}
try{const p=await fe('/profile');T('profile').innerHTML=Object.entries(p).filter(function(e){return e[0]!=='updated_at'}).map(function(e){return stat(e[0],typeof e[1]==='object'?JSON.stringify(e[1]):e[1]||'\\u2014')}).join('')}catch(e){T('profile').textContent=e}

try{const k=await fe('/knowledge-graph/stats');T('kg').innerHTML=stat('Entities',k.entities||0)+stat('Relations',k.relations||0)+
(k.top_entities||[]).slice(0,5).map(function(e){return stat(e.name,e.type+' ('+e.mentions+'x)')}).join('')}catch(e){T('kg').textContent='disabled'}

try{
const ts=await fe('/token-stats');
if(costChart&&ts.by_model){
const models=Object.keys(ts.by_model).slice(0,10);
costChart.data.labels=models.map(function(m){return m.split('/').pop()});
costChart.data.datasets[0].data=models.map(function(m){return (ts.by_model[m].cost_usd||0).toFixed(4)});
costChart.data.datasets[1].data=models.map(function(m){return ts.by_model[m].calls||0});
costChart.update();}
}catch(e){}

try{const w=await fe('/working-memory');T('wm').innerHTML=stat('Active',w.active_sessions||0)+stat('Total',w.total_sessions||0)+stat('Max',w.max_sessions||0)}catch(e){T('wm').textContent='n/a'}
try{const p=await fe('/plugins');T('plugins').innerHTML=(p.plugins||[]).map(function(pl){return stat(pl.name,pl.tools.length+' tools')}).join('')||stat('Plugins','none')}catch(e){T('plugins').textContent='n/a'}

try{const r=await fe('/routes');T('routes').textContent=JSON.stringify(r.task_routes,null,2)}catch(e){T('routes').textContent=e}
T('last-update').textContent='updated '+new Date().toLocaleTimeString()
}
if(!localStorage.getItem('bt')){const t=prompt('Bridge token:');if(t)localStorage.setItem('bt',t)}
initCharts();refresh();setInterval(refresh,30000);
</script></body></html>"""


def get_dashboard_html() -> str:
    return DASHBOARD_HTML
