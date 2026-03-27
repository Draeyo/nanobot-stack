"""Admin web UI — single-page application for nanobot-stack management.

Served at GET /admin.  Uses Alpine.js for reactivity and Chart.js for charts.
The entire HTML/CSS/JS is stored as Python string constants (same pattern as
dashboard.py) — no build step, no static files, no Jinja2.
"""
from __future__ import annotations
import os

ADMIN_ENABLED = os.getenv("ADMIN_UI_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
ADMIN_CSS = """
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--text:#e0e0e0;--muted:#888;
--green:#22c55e;--red:#ef4444;--yellow:#eab308;--blue:#3b82f6;--purple:#8b5cf6;
--cyan:#06b6d4;--input-bg:#12141d;--hover:#252836;--active-tab:#3b82f6}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--text);font-size:14px;line-height:1.5}
[x-cloak]{display:none!important}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}

/* Nav */
.topnav{background:var(--card);border-bottom:1px solid var(--border);padding:0 16px;
display:flex;align-items:center;position:sticky;top:0;z-index:100;overflow-x:auto}
.topnav .brand{font-weight:700;font-size:16px;color:var(--cyan);padding:12px 16px 12px 0;
white-space:nowrap;border-right:1px solid var(--border);margin-right:8px}
.topnav a.tab{padding:12px 14px;color:var(--muted);white-space:nowrap;font-size:13px;
border-bottom:2px solid transparent;transition:all .15s}
.topnav a.tab:hover{color:var(--text);text-decoration:none;background:var(--hover)}
.topnav a.tab.active{color:var(--active-tab);border-bottom-color:var(--active-tab)}

/* Layout */
main{max-width:1400px;margin:0 auto;padding:16px}
section{min-height:200px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-bottom:16px}
.grid-wide{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
.card h3{font-size:14px;color:var(--muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}

/* Tables */
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--muted);
font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.tbl td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
.tbl tr:hover td{background:var(--hover)}

/* Forms */
.form-row{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
input[type=text],input[type=number],input[type=password],textarea,select{
background:var(--input-bg);border:1px solid var(--border);border-radius:4px;
color:var(--text);padding:6px 10px;font-size:13px;font-family:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--blue)}
textarea{resize:vertical;min-height:60px;width:100%}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:4px;padding:5px 12px;border:none;
border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;color:#fff;transition:opacity .15s}
.btn:hover{opacity:.85;text-decoration:none}
.btn-blue{background:var(--blue)}.btn-green{background:var(--green)}
.btn-red{background:var(--red)}.btn-yellow{background:var(--yellow);color:#000}
.btn-muted{background:var(--border);color:var(--text)}
.btn-sm{padding:3px 8px;font-size:11px}

/* Badges */
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge-green{background:rgba(34,197,94,.15);color:var(--green)}
.badge-red{background:rgba(239,68,68,.15);color:var(--red)}
.badge-yellow{background:rgba(234,179,8,.15);color:var(--yellow)}
.badge-blue{background:rgba(59,130,246,.15);color:var(--blue)}
.badge-muted{background:rgba(136,136,136,.15);color:var(--muted)}

/* Stat */
.stat{font-size:28px;font-weight:700;color:var(--text)}
.stat-label{font-size:12px;color:var(--muted);margin-top:2px}
.mono{font-family:'Fira Code',Consolas,monospace;font-size:12px}

/* Diff */
.diff-line{font-family:monospace;font-size:12px;padding:1px 8px;white-space:pre-wrap;word-break:break-all}
.diff-add{background:rgba(34,197,94,.12);color:var(--green)}
.diff-del{background:rgba(239,68,68,.12);color:var(--red)}
.diff-hdr{color:var(--cyan);font-weight:600}

/* Chat */
.chat-wrap{display:flex;flex-direction:column;height:60vh;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.chat-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.chat-bubble{max-width:80%;padding:8px 12px;border-radius:12px;font-size:13px;line-height:1.5;word-wrap:break-word}
.chat-user{align-self:flex-end;background:var(--blue);color:#fff;border-bottom-right-radius:4px}
.chat-bot{align-self:flex-start;background:var(--card);border:1px solid var(--border);border-bottom-left-radius:4px}
.chat-input-row{display:flex;border-top:1px solid var(--border)}
.chat-input-row textarea{flex:1;border:none;border-radius:0;padding:10px;resize:none;height:48px}
.chat-input-row .btn{border-radius:0;height:48px;padding:0 20px}

/* Pipeline progress */
.pipeline-steps{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.pipe-step{padding:3px 8px;border-radius:4px;font-size:11px;background:var(--border);color:var(--muted)}
.pipe-step.done{background:rgba(34,197,94,.15);color:var(--green)}
.pipe-step.active{background:rgba(59,130,246,.15);color:var(--blue);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}

/* Modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;display:flex;align-items:center;justify-content:center}
.modal{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;max-width:700px;
width:90%;max-height:80vh;overflow-y:auto}
.modal h3{margin-bottom:12px}

/* Chart container */
.chart-container{position:relative;height:220px}

/* Subsection tabs */
.subtabs{display:flex;gap:4px;margin-bottom:12px}
.subtabs .btn{border-radius:16px}
.subtabs .btn.active{background:var(--blue)}

/* Utility */
.mb-8{margin-bottom:8px}.mb-12{margin-bottom:12px}.mb-16{margin-bottom:16px}
.mt-8{margin-top:8px}.mt-12{margin-top:12px}
.text-muted{color:var(--muted)}.text-green{color:var(--green)}.text-red{color:var(--red)}
.text-sm{font-size:12px}.text-xs{font-size:11px}
.flex{display:flex}.flex-between{display:flex;justify-content:space-between;align-items:center}
.gap-8{gap:8px}.gap-12{gap:12px}
.truncate{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;display:inline-block}
.pre-wrap{white-space:pre-wrap;font-family:monospace;font-size:12px;background:var(--input-bg);
padding:8px;border-radius:4px;max-height:300px;overflow-y:auto}
.warn-banner{background:rgba(234,179,8,.1);border:1px solid var(--yellow);border-radius:6px;
padding:10px 14px;margin-bottom:12px;color:var(--yellow);font-size:13px}
"""

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
ADMIN_NAV = """
<nav class="topnav">
  <span class="brand">nanobot admin</span>
  <template x-for="t in tabs" :key="t.id">
    <a :href="'#'+t.id" class="tab" :class="{'active':tab===t.id}"
       @click.prevent="tab=t.id;loadTab()" x-text="t.label"></a>
  </template>
</nav>
"""

# ---------------------------------------------------------------------------
# Section: Analytics
# ---------------------------------------------------------------------------
SECTION_ANALYTICS = """
<section x-show="tab==='analytics'" x-cloak>
  <div class="flex-between mb-12">
    <h2>Analytics</h2>
    <div class="flex gap-8">
      <label class="text-sm text-muted"><input type="checkbox" x-model="autoRefresh"> Auto-refresh</label>
      <button class="btn btn-muted btn-sm" @click="loadAnalytics()">Refresh</button>
    </div>
  </div>
  <div class="grid">
    <div class="card"><h3>Health</h3>
      <template x-if="health"><div>
        <div class="stat" x-text="health.status||'ok'"></div>
        <div class="stat-label" x-text="'Qdrant: '+(health.checks?.qdrant?.ok?'connected':'down')"></div>
        <div class="stat-label" x-text="'Collections: '+(health.checks?.qdrant?.collections||0)"></div>
      </div></template>
    </div>
    <div class="card"><h3>Circuit Breakers</h3>
      <template x-if="analytics.cbs"><div>
        <template x-for="(v,k) in analytics.cbs" :key="k">
          <div class="mb-8"><span class="mono" x-text="k"></span>: <span :class="v.state==='closed'?'badge badge-green':'badge badge-red'" x-text="v.state"></span>
            <span class="text-xs text-muted" x-text="'fails:'+v.fail_count"></span></div>
        </template>
      </div></template>
    </div>
    <div class="card"><h3>Caches</h3>
      <template x-if="analytics.cache"><div>
        <div class="text-sm">Embed: <span class="text-green" x-text="analytics.cache.embedding?.hits||0"></span> hits /
          <span class="text-red" x-text="analytics.cache.embedding?.misses||0"></span> misses</div>
        <div class="text-sm">LLM: <span class="text-green" x-text="analytics.cache.llm?.hits||0"></span> hits /
          <span class="text-red" x-text="analytics.cache.llm?.misses||0"></span> misses</div>
      </div></template>
    </div>
    <div class="card"><h3>Rate Limits</h3>
      <template x-if="analytics.rates"><div>
        <template x-for="(v,k) in analytics.rates" :key="k">
          <div class="text-sm mb-4"><span class="mono" x-text="k"></span>: <span x-text="v.available?.toFixed(1)||0"></span>/<span x-text="v.capacity||0"></span></div>
        </template>
      </div></template>
    </div>
    <div class="card"><h3>Feedback</h3>
      <template x-if="analytics.feedback"><div>
        <div class="stat" x-text="analytics.feedback.total_events||0"></div>
        <div class="stat-label">total feedback events</div>
        <div class="text-sm mt-8" x-text="'Boosted chunks: '+(analytics.feedback.boosted_chunks||0)"></div>
      </div></template>
    </div>
    <div class="card"><h3>Ingestion</h3>
      <template x-if="analytics.ingest"><div>
        <div class="stat" x-text="analytics.ingest.status||'idle'"></div>
        <div class="stat-label" x-text="'Total indexed: '+(analytics.ingest.total_indexed||0)"></div>
      </div></template>
    </div>
    <div class="card"><h3>Profile</h3>
      <template x-if="analytics.profile"><div>
        <template x-for="(v,k) in analytics.profile" :key="k">
          <div class="text-sm mb-4" x-show="k!=='raw'"><span class="text-muted" x-text="k+': '"></span><span x-text="typeof v==='object'?JSON.stringify(v):v"></span></div>
        </template>
      </div></template>
    </div>
    <div class="card"><h3>Knowledge Graph</h3>
      <template x-if="analytics.kg"><div>
        <div class="stat" x-text="analytics.kg.entity_count||0"></div>
        <div class="stat-label">entities</div>
        <div class="text-sm mt-8" x-text="'Relations: '+(analytics.kg.relation_count||0)"></div>
      </div></template>
    </div>
  </div>
  <div class="grid-wide">
    <div class="card"><h3>Token Usage by Model</h3><div class="chart-container"><canvas id="costChart"></canvas></div></div>
    <div class="card"><h3>Cache Performance</h3><div class="chart-container"><canvas id="cacheChart"></canvas></div></div>
  </div>
  <div class="grid">
    <div class="card"><h3>Working Memory</h3>
      <template x-if="analytics.wm"><div>
        <div class="text-sm">Active sessions: <strong x-text="analytics.wm.active_sessions||0"></strong></div>
        <div class="text-sm">Max sessions: <strong x-text="analytics.wm.max_sessions||0"></strong></div>
      </div></template>
    </div>
    <div class="card"><h3>Plugins</h3>
      <template x-if="analytics.plugins"><div>
        <div class="stat" x-text="analytics.plugins.plugins?.length||0"></div>
        <div class="stat-label">loaded plugins</div>
      </div></template>
    </div>
    <div class="card"><h3>Routes</h3>
      <template x-if="analytics.routes"><div>
        <div class="text-sm">Profiles: <strong x-text="Object.keys(analytics.routes.profiles||{}).length"></strong></div>
        <div class="text-sm">Task routes: <strong x-text="Object.keys(analytics.routes.task_routes||{}).length"></strong></div>
      </div></template>
    </div>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Settings
# ---------------------------------------------------------------------------
SECTION_SETTINGS = """
<section x-show="tab==='settings'" x-cloak>
  <h2 class="mb-12">Settings</h2>
  <div class="warn-banner" x-show="!settingsConfigWriterEnabled">
    CONFIG_WRITER_ENABLED is false. Setting changes via this UI require the config writer to be enabled.
  </div>
  <div class="subtabs mb-12">
    <template x-for="sec in settingSections" :key="sec">
      <button class="btn btn-muted btn-sm" :class="{'active':settingFilter===sec}"
              @click="settingFilter=sec" x-text="sec"></button>
    </template>
    <button class="btn btn-muted btn-sm" :class="{'active':settingFilter===''}"
            @click="settingFilter=''">all</button>
  </div>
  <div class="card">
    <table class="tbl">
      <thead><tr><th>Key</th><th>Value</th><th>Default</th><th>Description</th><th></th></tr></thead>
      <tbody>
        <template x-for="s in filteredSettings" :key="s.key">
          <tr>
            <td><span class="mono" x-text="s.key"></span></td>
            <td>
              <template x-if="editingSetting===s.key">
                <div class="form-row">
                  <template x-if="s.choices&&s.choices.length">
                    <select x-model="editingValue" style="min-width:120px">
                      <template x-for="c in s.choices"><option :value="c" x-text="c"></option></template>
                    </select>
                  </template>
                  <template x-if="!s.choices||!s.choices.length">
                    <input type="text" x-model="editingValue" style="min-width:180px">
                  </template>
                </div>
              </template>
              <template x-if="editingSetting!==s.key">
                <span class="mono" x-text="s.value"></span>
              </template>
            </td>
            <td class="text-muted text-xs" x-text="s.default"></td>
            <td class="text-xs" x-text="s.description" style="max-width:280px"></td>
            <td>
              <template x-if="editingSetting===s.key">
                <div class="flex gap-8">
                  <button class="btn btn-green btn-sm" @click="proposeSetting(s.key)">Propose</button>
                  <button class="btn btn-muted btn-sm" @click="editingSetting=''">Cancel</button>
                </div>
              </template>
              <template x-if="editingSetting!==s.key && !s.sensitive">
                <button class="btn btn-muted btn-sm" @click="editingSetting=s.key;editingValue=s.value">Edit</button>
              </template>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Tools
# ---------------------------------------------------------------------------
SECTION_TOOLS = """
<section x-show="tab==='tools'" x-cloak>
  <h2 class="mb-12">Tools & Routing</h2>
  <div class="grid-wide">
    <div class="card">
      <h3>Loaded Plugins</h3>
      <template x-if="plugins.plugins&&plugins.plugins.length">
        <table class="tbl">
          <thead><tr><th>Plugin</th><th>Tools</th><th>Hooks</th><th>Has Router</th></tr></thead>
          <tbody>
            <template x-for="p in plugins.plugins" :key="p.name">
              <tr>
                <td class="mono" x-text="p.name"></td>
                <td x-text="p.tools?.length||0"></td>
                <td x-text="p.hooks?.length||0"></td>
                <td><span :class="p.has_router?'badge badge-green':'badge badge-muted'" x-text="p.has_router?'yes':'no'"></span></td>
              </tr>
            </template>
          </tbody>
        </table>
      </template>
      <p x-show="!plugins.plugins||!plugins.plugins.length" class="text-muted">No plugins loaded</p>
    </div>
    <div class="card">
      <h3>Model Routing</h3>
      <template x-if="routes"><div>
        <div class="text-sm mb-8"><strong x-text="Object.keys(routes.profiles||{}).length"></strong> profiles,
          <strong x-text="Object.keys(routes.task_routes||{}).length"></strong> task routes</div>
        <div style="max-height:300px;overflow-y:auto">
          <table class="tbl">
            <thead><tr><th>Task</th><th>Chain</th></tr></thead>
            <tbody>
              <template x-for="(chain,task) in (routes.task_routes||{})" :key="task">
                <tr>
                  <td class="mono" x-text="task"></td>
                  <td class="text-xs" x-text="chain.join(' → ')"></td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div></template>
    </div>
  </div>
  <div class="card mt-12">
    <h3>Route Preview</h3>
    <div class="form-row">
      <input type="text" x-model="routePreviewTask" placeholder="Task type (e.g. retrieval_answer)" style="width:300px">
      <button class="btn btn-blue btn-sm" @click="previewRoute()">Preview</button>
    </div>
    <template x-if="routePreviewResult">
      <div class="pre-wrap mt-8" x-text="JSON.stringify(routePreviewResult,null,2)"></div>
    </template>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Vector DB
# ---------------------------------------------------------------------------
SECTION_VECTOR_DB = """
<section x-show="tab==='vectordb'" x-cloak>
  <h2 class="mb-12">Vector Database</h2>
  <div class="grid mb-16">
    <template x-for="c in collections" :key="c.name">
      <div class="card" @click="browseCollection=c.name;scrollOffset=null;scrollPoints=[];scrollCollection()"
           style="cursor:pointer">
        <h3 x-text="c.name"></h3>
        <div class="stat" x-text="c.points_count??'?'"></div>
        <div class="stat-label">points</div>
        <span class="badge" :class="c.status==='green'?'badge-green':'badge-yellow'" x-text="c.status"></span>
      </div>
    </template>
  </div>
  <div class="card mb-16">
    <h3>Search Tester</h3>
    <div class="form-row">
      <input type="text" x-model="searchQuery" placeholder="Search query..." style="flex:1">
      <input type="number" x-model.number="searchLimit" min="1" max="20" style="width:60px" placeholder="5">
      <button class="btn btn-blue btn-sm" @click="runSearch()">Search</button>
    </div>
    <template x-if="searchResults.length">
      <div class="mt-8" style="max-height:400px;overflow-y:auto">
        <template x-for="(r,i) in searchResults" :key="i">
          <div class="card mb-8" style="background:var(--input-bg)">
            <div class="flex-between mb-4">
              <span class="badge badge-blue" x-text="'#'+(i+1)+' score: '+(r.score?.toFixed(4)||'?')"></span>
              <span class="text-xs text-muted" x-text="r.collection+' / '+(r.source_name||'')"></span>
            </div>
            <div class="text-sm" x-text="(r.text||'').substring(0,300)+(r.text?.length>300?'...':'')"></div>
          </div>
        </template>
      </div>
    </template>
  </div>
  <div class="card" x-show="browseCollection">
    <div class="flex-between mb-8">
      <h3 x-text="'Browse: '+browseCollection"></h3>
      <button class="btn btn-muted btn-sm" @click="browseCollection='';scrollPoints=[]">Close</button>
    </div>
    <div style="max-height:400px;overflow-y:auto">
      <template x-for="(p,i) in scrollPoints" :key="p.id">
        <div class="card mb-8" style="background:var(--input-bg)">
          <div class="text-xs text-muted mb-4" x-text="'ID: '+p.id"></div>
          <div class="text-sm" x-text="(p.payload?.text||JSON.stringify(p.payload)||'').substring(0,200)"></div>
          <div class="text-xs text-muted mt-4" x-text="'tags: '+(p.payload?.tags||[]).join(', ')"></div>
        </div>
      </template>
    </div>
    <button class="btn btn-muted btn-sm mt-8" x-show="scrollNextOffset"
            @click="scrollCollection()">Load more</button>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Logs
# ---------------------------------------------------------------------------
SECTION_LOGS = """
<section x-show="tab==='logs'" x-cloak>
  <div class="flex-between mb-12">
    <h2>Audit Log</h2>
    <div class="flex gap-8">
      <select x-model="logMethodFilter" @change="loadLogs()">
        <option value="">All methods</option>
        <option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option>
      </select>
      <input type="text" x-model="logPathFilter" placeholder="Path filter..." @keyup.enter="loadLogs()">
      <button class="btn btn-muted btn-sm" @click="loadLogs()">Refresh</button>
    </div>
  </div>
  <div class="card">
    <div style="max-height:500px;overflow-y:auto">
      <table class="tbl">
        <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>IP</th><th>ms</th></tr></thead>
        <tbody>
          <template x-for="e in auditLogs" :key="e.ts+e.path">
            <tr>
              <td class="text-xs mono" x-text="e.ts?.substring(11,19)||e.event||''"></td>
              <td><span class="badge" :class="e.method==='GET'?'badge-green':e.method==='POST'?'badge-blue':'badge-yellow'" x-text="e.method||e.event||''"></span></td>
              <td class="mono truncate" x-text="e.path||e.action_id||e.change_id||''"></td>
              <td><span :class="(e.status>=400)?'text-red':'text-green'" x-text="e.status||''"></span></td>
              <td class="text-xs text-muted" x-text="e.ip||''"></td>
              <td class="text-xs" x-text="e.ms||''"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <div class="flex-between mt-8">
      <span class="text-xs text-muted" x-text="'Total: '+(auditTotal||0)+' entries'"></span>
      <div class="flex gap-8">
        <button class="btn btn-muted btn-sm" x-show="auditOffset>0" @click="auditOffset-=100;loadLogs()">Prev</button>
        <button class="btn btn-muted btn-sm" x-show="auditLogs.length>=100" @click="auditOffset+=100;loadLogs()">Next</button>
      </div>
    </div>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Chat
# ---------------------------------------------------------------------------
SECTION_CHAT = """
<section x-show="tab==='chat'" x-cloak>
  <h2 class="mb-12">Chat with Agent</h2>
  <div class="grid-wide">
    <div>
      <div class="pipeline-steps mb-8">
        <template x-for="s in pipelineSteps" :key="s.name">
          <span class="pipe-step" :class="s.status" x-text="s.name"></span>
        </template>
      </div>
      <div class="chat-wrap">
        <div class="chat-msgs" id="chatMsgs">
          <template x-for="(m,i) in chatMessages" :key="i">
            <div class="chat-bubble" :class="m.role==='user'?'chat-user':'chat-bot'">
              <div x-html="renderMd(m.content)"></div>
            </div>
          </template>
          <div class="chat-bubble chat-bot" x-show="chatStreaming" x-html="renderMd(chatStreamText)"></div>
        </div>
        <div class="chat-input-row">
          <textarea x-model="chatInput" @keydown.enter.prevent="if(!$event.shiftKey)sendChat()"
                    placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
                    :disabled="chatStreaming"></textarea>
          <button class="btn btn-blue" @click="sendChat()" :disabled="chatStreaming">Send</button>
        </div>
      </div>
    </div>
    <div>
      <div class="card mb-12">
        <h3>Pipeline Options</h3>
        <label class="text-sm"><input type="checkbox" x-model="chatOpts.auto_classify"> Auto-classify</label><br>
        <label class="text-sm"><input type="checkbox" x-model="chatOpts.enable_hyde"> HyDE rewriting</label><br>
        <label class="text-sm"><input type="checkbox" x-model="chatOpts.enable_citations"> Citations</label><br>
        <label class="text-sm"><input type="checkbox" x-model="chatOpts.enable_self_critique"> Self-critique</label><br>
        <div class="form-row mt-8">
          <label class="text-sm text-muted">Session ID:</label>
          <input type="text" x-model="chatSessionId" placeholder="auto" style="width:160px">
        </div>
      </div>
      <div class="card" x-show="chatSources.length">
        <h3>Sources</h3>
        <template x-for="(s,i) in chatSources" :key="i">
          <div class="text-sm mb-4"><span class="badge badge-blue" x-text="'['+s.num+']'"></span> <span x-text="s.source||s.collection||''"></span></div>
        </template>
      </div>
    </div>
  </div>

  <!-- Voice Interface block — shown only if voice is enabled -->
  <div x-data="voiceUI()" x-show="voiceEnabled" x-cloak class="mt-6 border-t pt-4">
    <h3 class="font-semibold text-gray-700 mb-3">Interface Vocale</h3>

    <!-- Record button -->
    <div class="flex items-center gap-3 mb-3">
      <button
        @click="toggleRecording()"
        :class="recording ? 'bg-red-500 hover:bg-red-600' : 'bg-blue-500 hover:bg-blue-600'"
        class="text-white font-semibold px-4 py-2 rounded transition"
      >
        <span x-text="recording ? 'Arreter' : 'Parler'"></span>
      </button>
      <div x-show="recording" class="text-sm text-red-500 animate-pulse">Enregistrement...</div>
      <div x-show="processing" class="text-sm text-blue-500 animate-pulse">Traitement...</div>
    </div>

    <!-- Audio level visualizer -->
    <div x-show="recording" class="mb-3 h-4 bg-gray-200 rounded overflow-hidden">
      <div class="h-full bg-green-400 transition-all duration-100"
           :style="`width: ${audioLevel}%`"></div>
    </div>

    <!-- Transcription preview -->
    <div x-show="transcription" class="mb-3">
      <div class="text-sm text-gray-500 mb-1">Transcription :</div>
      <div class="bg-gray-50 border rounded px-3 py-2 text-sm" x-text="transcription"></div>
    </div>

    <!-- Audio player for response -->
    <div x-show="audioUrl" class="mb-3">
      <audio :src="audioUrl" controls autoplay class="w-full"></audio>
      <div x-show="responseText" class="mt-2 text-sm text-gray-600" x-text="responseText"></div>
    </div>

    <!-- Voice settings (collapsible) -->
    <details class="text-sm mt-3">
      <summary class="cursor-pointer text-gray-500 hover:text-gray-700">Parametres voix</summary>
      <div class="mt-2 grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-500 mb-1">Voix TTS</label>
          <select x-model="selectedVoice" class="border rounded px-2 py-1 text-sm w-full">
            <option value="fr_siwis">fr_siwis (naturelle)</option>
            <option value="fr_siwis_low">fr_siwis_low (rapide)</option>
            <option value="fr_upmc_pierre">fr_upmc_pierre</option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Langue STT</label>
          <select x-model="sttLanguage" class="border rounded px-2 py-1 text-sm w-full">
            <option value="fr">Francais</option>
            <option value="en">English</option>
            <option value="">Auto-detection</option>
          </select>
        </div>
      </div>
      <button @click="testVoice()" class="mt-2 text-blue-500 hover:underline text-xs">
        Tester la voix
      </button>
    </details>

    <!-- Disabled notice (shown when voice feature is off) -->
    <div x-show="!voiceEnabled" class="text-sm text-gray-400 italic">
      Interface vocale desactivee — definir <code>VOICE_ENABLED=true</code>
      et redemarrer les services Docker (<code>piper</code>).
    </div>
  </div>

  <script>
  function voiceUI() {
    return {
      voiceEnabled: false,
      recording: false,
      processing: false,
      audioLevel: 0,
      transcription: '',
      responseText: '',
      audioUrl: null,
      selectedVoice: 'fr_siwis',
      sttLanguage: 'fr',
      _mediaRecorder: null,
      _chunks: [],
      _analyser: null,
      _animFrame: null,

      async init() {
        try {
          const r = await fetch('/api/voice/status');
          const d = await r.json();
          this.voiceEnabled = d.enabled;
        } catch(e) { /* voice not available */ }
      },

      async toggleRecording() {
        if (this.recording) {
          this._stopRecording();
        } else {
          await this._startRecording();
        }
      },

      async _startRecording() {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus' : 'audio/mp4';
        this._mediaRecorder = new MediaRecorder(stream, { mimeType });
        this._chunks = [];
        this._mediaRecorder.ondataavailable = e => this._chunks.push(e.data);
        this._mediaRecorder.onstop = () => this._sendAudio(mimeType);

        const ctx = new AudioContext();
        const src = ctx.createMediaStreamSource(stream);
        this._analyser = ctx.createAnalyser();
        src.connect(this._analyser);
        this._animateLevel();

        this._mediaRecorder.start();
        this.recording = true;
        this.transcription = '';
        this.audioUrl = null;
        this.responseText = '';
      },

      _stopRecording() {
        this._mediaRecorder && this._mediaRecorder.stop();
        this.recording = false;
        cancelAnimationFrame(this._animFrame);
        this.audioLevel = 0;
      },

      _animateLevel() {
        if (!this._analyser) return;
        const data = new Uint8Array(this._analyser.frequencyBinCount);
        const tick = () => {
          this._analyser.getByteFrequencyData(data);
          const avg = data.reduce((s, v) => s + v, 0) / data.length;
          this.audioLevel = Math.min(100, avg * 1.5);
          this._animFrame = requestAnimationFrame(tick);
        };
        tick();
      },

      async _sendAudio(mimeType) {
        this.processing = true;
        try {
          const ext = mimeType.startsWith('audio/webm') ? 'webm' : 'mp4';
          const blob = new Blob(this._chunks, { type: mimeType });
          const form = new FormData();
          form.append('file', blob, `recording.${ext}`);
          form.append('session_id', '');

          const r = await fetch('/api/voice/chat', { method: 'POST', body: form });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);

          this.transcription = r.headers.get('x-transcription') || '';
          this.responseText = r.headers.get('x-response-text') || '';

          const audioBuf = await r.arrayBuffer();
          const audioBlob = new Blob([audioBuf], { type: 'audio/mpeg' });
          this.audioUrl = URL.createObjectURL(audioBlob);
        } catch (e) {
          this.transcription = `Erreur : ${e.message}`;
        } finally {
          this.processing = false;
        }
      },

      async testVoice() {
        const r = await fetch('/api/voice/synthesize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: 'Bonjour, le systeme vocal est operationnel.', voice: this.selectedVoice }),
        });
        if (r.ok) {
          const buf = await r.arrayBuffer();
          const blob = new Blob([buf], { type: 'audio/mpeg' });
          new Audio(URL.createObjectURL(blob)).play();
        }
      },
    };
  }
  </script>

</section>
"""

# ---------------------------------------------------------------------------
# Section: Channels
# ---------------------------------------------------------------------------
SECTION_CHANNELS = """
<section x-show="tab==='channels'" x-cloak>
  <h2 class="mb-12">Channel Management</h2>
  <div class="grid mb-16">
    <template x-for="(info,name) in channelStatus" :key="name">
      <div class="card">
        <h3 x-text="name"></h3>
        <span class="badge" :class="info.running?'badge-green':'badge-muted'" x-text="info.running?'running':'stopped'"></span>
        <span class="badge badge-blue" x-show="info.configured">configured</span>
        <div class="text-sm text-red mt-8" x-show="info.error" x-text="info.error"></div>
      </div>
    </template>
    <div class="card" x-show="!Object.keys(channelStatus||{}).length">
      <p class="text-muted">No channel adapters registered</p>
    </div>
  </div>
  <div class="card mb-16">
    <div class="flex-between mb-8">
      <h3>Pending Pairings <span class="badge badge-blue" x-text="dmPolicy"></span></h3>
      <button class="btn btn-muted btn-sm" @click="loadChannels()">Refresh</button>
    </div>
    <template x-if="pendingPairings.length">
      <table class="tbl">
        <thead><tr><th>Code</th><th>Platform</th><th>User</th><th>Created</th><th>Expires</th><th></th></tr></thead>
        <tbody>
          <template x-for="p in pendingPairings" :key="p.code">
            <tr>
              <td class="mono" x-text="p.code"></td>
              <td x-text="p.platform_name"></td>
              <td class="mono text-xs" x-text="p.platform_id"></td>
              <td class="text-xs" x-text="p.created_at?.substring(0,19)"></td>
              <td class="text-xs" x-text="p.expires_at?.substring(0,19)"></td>
              <td>
                <button class="btn btn-green btn-sm" @click="approvePairing(p.code)">Approve</button>
                <button class="btn btn-red btn-sm" @click="rejectPairing(p.code)">Reject</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
    <p x-show="!pendingPairings.length" class="text-muted text-sm">No pending pairing requests</p>
  </div>
  <div class="card">
    <h3 class="mb-8">Approved Users</h3>
    <template x-if="approvedUsers.length">
      <table class="tbl">
        <thead><tr><th>Platform ID</th><th>Approved At</th><th>By</th><th></th></tr></thead>
        <tbody>
          <template x-for="u in approvedUsers" :key="u.platform_id">
            <tr>
              <td class="mono" x-text="u.platform_id"></td>
              <td class="text-xs" x-text="u.approved_at?.substring(0,19)"></td>
              <td class="text-xs" x-text="u.approved_by"></td>
              <td><button class="btn btn-red btn-sm" @click="revokeUser(u.platform_id)">Revoke</button></td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
    <p x-show="!approvedUsers.length" class="text-muted text-sm">No approved users</p>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Elevated Shell
# ---------------------------------------------------------------------------
SECTION_SHELL = """
<section x-show="tab==='shell'" x-cloak>
  <h2 class="mb-12">Elevated Shell</h2>
  <div class="card mb-16">
    <h3>Propose Command</h3>
    <div class="form-row">
      <input type="text" x-model="proposeCmd" placeholder="e.g. systemctl restart nanobot-rag-bridge" style="flex:1">
      <input type="text" x-model="proposeDesc" placeholder="Description (optional)" style="width:200px">
      <button class="btn btn-blue btn-sm" @click="proposeAction()">Propose</button>
    </div>
    <div class="text-sm text-green mt-8" x-show="proposeResult" x-text="proposeResult"></div>
  </div>
  <div class="card mb-16">
    <div class="flex-between mb-8">
      <h3>Pending Actions</h3>
      <button class="btn btn-muted btn-sm" @click="loadShell()">Refresh</button>
    </div>
    <template x-if="pendingActions.length">
      <table class="tbl">
        <thead><tr><th>ID</th><th>Command</th><th>Description</th><th>Proposed</th><th></th></tr></thead>
        <tbody>
          <template x-for="a in pendingActions" :key="a.id">
            <tr>
              <td class="mono text-xs" x-text="a.id"></td>
              <td class="mono" x-text="a.command"></td>
              <td class="text-xs" x-text="a.description"></td>
              <td class="text-xs" x-text="a.proposed_at?.substring(0,19)"></td>
              <td>
                <button class="btn btn-green btn-sm" @click="approveAction(a.id)">Approve</button>
                <button class="btn btn-red btn-sm" @click="rejectAction(a.id)">Reject</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
    <p x-show="!pendingActions.length" class="text-muted text-sm">No pending actions</p>
  </div>
  <div class="card mb-16">
    <h3 class="mb-8">Action History</h3>
    <div style="max-height:400px;overflow-y:auto">
      <table class="tbl">
        <thead><tr><th>ID</th><th>Command</th><th>Status</th><th>Time</th><th>Result</th></tr></thead>
        <tbody>
          <template x-for="a in actionHistory" :key="a.id">
            <tr>
              <td class="mono text-xs" x-text="a.id"></td>
              <td class="mono text-xs" x-text="a.command"></td>
              <td><span class="badge" :class="{'badge-green':a.status==='executed','badge-red':a.status==='rejected','badge-yellow':a.status==='expired','badge-blue':a.status==='approved','badge-muted':a.status==='pending'}" x-text="a.status"></span></td>
              <td class="text-xs" x-text="a.proposed_at?.substring(0,19)"></td>
              <td class="text-xs"><span x-show="a.result?.stdout" class="text-green" x-text="(a.result?.stdout||'').substring(0,100)"></span>
                <span x-show="a.result?.stderr" class="text-red" x-text="(a.result?.stderr||'').substring(0,100)"></span></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <details>
      <summary class="text-sm text-muted" style="cursor:pointer">Command Allow-List</summary>
      <div class="mt-8">
        <template x-for="(spec,bin) in elevatedCommands" :key="bin">
          <div class="text-sm mb-4"><span class="mono" x-text="bin"></span>:
            <span class="text-muted" x-text="spec.subcommands==='*'?'any subcommand':spec.subcommands.join(', ')"></span></div>
        </template>
      </div>
    </details>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Config Writer
# ---------------------------------------------------------------------------
SECTION_CONFIG = """
<section x-show="tab==='config'" x-cloak>
  <h2 class="mb-12">Config Writer</h2>
  <div class="card mb-16">
    <div class="flex-between mb-8">
      <h3>Pending Changes</h3>
      <button class="btn btn-muted btn-sm" @click="loadConfig()">Refresh</button>
    </div>
    <template x-if="pendingConfigs.length">
      <table class="tbl">
        <thead><tr><th>ID</th><th>File</th><th>Status</th><th>Description</th><th>Proposed</th><th></th></tr></thead>
        <tbody>
          <template x-for="c in pendingConfigs" :key="c.id">
            <tr>
              <td class="mono text-xs" x-text="c.id"></td>
              <td class="mono" x-text="c.file_name"></td>
              <td><span class="badge" :class="c.status==='validated'?'badge-green':'badge-yellow'" x-text="c.status"></span></td>
              <td class="text-xs" x-text="c.description"></td>
              <td class="text-xs" x-text="c.proposed_at?.substring(0,19)"></td>
              <td>
                <button class="btn btn-muted btn-sm" @click="previewDiff(c.id)">Diff</button>
                <button class="btn btn-green btn-sm" @click="applyConfig(c.id)">Apply</button>
                <button class="btn btn-red btn-sm" @click="rejectConfig(c.id)">Reject</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
    <p x-show="!pendingConfigs.length" class="text-muted text-sm">No pending changes</p>
  </div>
  <div class="card mb-16" x-show="diffPreview">
    <div class="flex-between mb-8">
      <h3>Diff Preview</h3>
      <button class="btn btn-muted btn-sm" @click="diffPreview=''">Close</button>
    </div>
    <div>
      <template x-for="(line,i) in diffPreview.split('\\n')" :key="i">
        <div class="diff-line" :class="{'diff-add':line.startsWith('+')&&!line.startsWith('+++'),'diff-del':line.startsWith('-')&&!line.startsWith('---'),'diff-hdr':line.startsWith('@@')||line.startsWith('---')||line.startsWith('+++')}" x-text="line"></div>
      </template>
    </div>
  </div>
  <div class="card">
    <h3 class="mb-8">Change History</h3>
    <div style="max-height:400px;overflow-y:auto">
      <table class="tbl">
        <thead><tr><th>ID</th><th>File</th><th>Status</th><th>Description</th><th>Time</th><th></th></tr></thead>
        <tbody>
          <template x-for="c in configHistory" :key="c.id">
            <tr>
              <td class="mono text-xs" x-text="c.id"></td>
              <td class="mono" x-text="c.file_name"></td>
              <td><span class="badge" :class="{'badge-green':c.status==='applied','badge-red':c.status==='rejected','badge-yellow':c.status==='expired','badge-blue':c.status==='rolled_back','badge-muted':c.status==='pending'||c.status==='validated'}" x-text="c.status"></span></td>
              <td class="text-xs" x-text="c.description"></td>
              <td class="text-xs" x-text="c.proposed_at?.substring(0,19)"></td>
              <td><button class="btn btn-yellow btn-sm" x-show="c.status==='applied'" @click="rollbackConfig(c.id)">Rollback</button></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Trust Policies (v10)
# ---------------------------------------------------------------------------
SECTION_TRUST = """
<section x-show="tab==='trust'" x-cloak>
  <h2 class="mb-12">Trust Policies</h2>
  <div class="card mb-12">
    <h3>Action Trust Levels</h3>
    <p class="text-xs text-muted mb-8">Configure how much autonomy the assistant has per action type. Changes take effect immediately.</p>
    <table class="tbl">
      <tr><th>Action Type</th><th>Trust Level</th><th>Successes</th><th>Failures</th><th>Actions</th></tr>
      <template x-for="p in trustPolicies" :key="p.action_type">
        <tr>
          <td x-text="p.action_type"></td>
          <td>
            <select :value="p.trust_level" @change="updateTrust(p.action_type,$event.target.value)" class="input" style="width:180px">
              <option value="auto">Auto</option>
              <option value="notify_then_execute">Notify then execute</option>
              <option value="approval_required">Approval required</option>
              <option value="blocked">Blocked</option>
            </select>
          </td>
          <td><span class="badge badge-green" x-text="p.successful_executions"></span></td>
          <td><span class="badge badge-red" x-text="p.failed_executions"></span></td>
          <td><button class="btn btn-blue btn-sm" @click="promoteTrust(p.action_type)">Promote</button></td>
        </tr>
      </template>
    </table>
  </div>
  <div class="card">
    <h3>Trust Audit Log</h3>
    <table class="tbl">
      <tr><th>Time</th><th>Action</th><th>Detail</th><th>Level</th><th>Outcome</th></tr>
      <template x-for="a in trustAudit" :key="a.id">
        <tr>
          <td class="text-xs" x-text="a.created_at?.substring(0,19)"></td>
          <td x-text="a.action_type"></td>
          <td class="text-xs" x-text="a.action_detail?.substring(0,60)"></td>
          <td><span class="badge badge-blue" x-text="a.trust_level"></span></td>
          <td><span :class="'badge badge-'+(a.outcome==='auto_executed'||a.outcome==='success'?'green':a.outcome==='blocked'?'red':'yellow')" x-text="a.outcome"></span></td>
        </tr>
      </template>
    </table>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Costs Dashboard (v10)
# ---------------------------------------------------------------------------
SECTION_COSTS = """
<section x-show="tab==='costs'" x-cloak>
  <h2 class="mb-12">Cost Dashboard</h2>
  <div class="grid">
    <div class="card">
      <h3>Daily Budget</h3>
      <template x-if="costData">
        <div>
          <div class="stat-value" x-text="'$'+(costData.daily_cost_used_cents/100).toFixed(2)+' / $'+(costData.daily_cost_budget_cents/100).toFixed(2)"></div>
          <div class="stat-label">Usage</div>
          <div class="mt-8" style="background:var(--border);border-radius:4px;height:8px;overflow:hidden">
            <div style="height:100%;border-radius:4px;transition:width .3s"
              :style="'width:'+Math.min(100,costData.usage_percent)+'%;background:var(--'+(costData.usage_percent>80?'red':costData.usage_percent>50?'yellow':'green')+')'"></div>
          </div>
          <div class="text-xs text-muted mt-4" x-text="costData.daily_tokens_used?.toLocaleString()+' / '+costData.daily_tokens_budget?.toLocaleString()+' tokens'"></div>
        </div>
      </template>
    </div>
    <div class="card">
      <h3>Budget Pressure</h3>
      <template x-if="costData">
        <div>
          <div class="stat-value" :class="costData.budget_pressure>0.8?'text-red':costData.budget_pressure>0.5?'text-yellow':'text-green'"
            x-text="(costData.budget_pressure*100).toFixed(0)+'%'"></div>
          <div class="stat-label" x-text="costData.budget_pressure>0.8?'High \u2014 models may downgrade to Ollama':'Normal'"></div>
        </div>
      </template>
    </div>
  </div>
  <div class="card">
    <h3>Usage by Model (today)</h3>
    <table class="tbl">
      <tr><th>Model</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th><th>Est. Cost</th></tr>
      <template x-for="m in costByModel" :key="m.model">
        <tr>
          <td x-text="m.model"></td>
          <td x-text="m.calls"></td>
          <td x-text="m.input_tokens?.toLocaleString()"></td>
          <td x-text="m.output_tokens?.toLocaleString()"></td>
          <td x-text="'$'+(m.cost_cents/100).toFixed(3)"></td>
        </tr>
      </template>
    </table>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Procedural Workflows (v10)
# ---------------------------------------------------------------------------
SECTION_WORKFLOWS = """
<section x-show="tab==='workflows'" x-cloak>
  <h2 class="mb-12">Procedural Workflows</h2>
  <p class="text-xs text-muted mb-12">Workflows learned from repeated action patterns. Enable with PROCEDURAL_MEMORY_ENABLED=true.</p>
  <div class="card">
    <table class="tbl">
      <tr><th>Trigger</th><th>Steps</th><th>Freq</th><th>Confidence</th><th>Auto-suggest</th><th>Last Seen</th></tr>
      <template x-for="w in workflows" :key="w.id">
        <tr>
          <td x-text="w.trigger_pattern"></td>
          <td x-text="JSON.parse(w.steps_json||'[]').length"></td>
          <td><span class="badge badge-blue" x-text="w.frequency"></span></td>
          <td><span :class="'badge badge-'+(w.confidence>=0.7?'green':w.confidence>=0.4?'yellow':'red')" x-text="(w.confidence*100).toFixed(0)+'%'"></span></td>
          <td>
            <button class="btn btn-sm" :class="w.auto_suggest?'btn-green':'btn-default'"
              @click="toggleWorkflow(w.id,!w.auto_suggest)" x-text="w.auto_suggest?'On':'Off'"></button>
          </td>
          <td class="text-xs" x-text="w.last_observed?.substring(0,10)"></td>
        </tr>
      </template>
      <template x-if="!workflows?.length">
        <tr><td colspan="6" class="text-muted text-center">No workflows learned yet</td></tr>
      </template>
    </table>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Agent Status (v10)
# ---------------------------------------------------------------------------
SECTION_AGENTS = """
<section x-show="tab==='agents'" x-cloak>
  <h2 class="mb-12">Agent Status</h2>
  <div class="grid">
    <template x-for="a in agentList" :key="a.name">
      <div class="card">
        <h3 x-text="a.name"></h3>
        <p class="text-xs text-muted" x-text="a.description"></p>
      </div>
    </template>
    <template x-if="!agentList?.length">
      <div class="card"><p class="text-muted">No agents registered. Enable with AGENT_ORCHESTRATOR_ENABLED=true.</p></div>
    </template>
  </div>
  <div class="card mt-12">
    <h3>Recent Executions</h3>
    <table class="tbl">
      <tr><th>Time</th><th>Agent</th><th>Task</th><th>Status</th><th>Tokens</th></tr>
      <template x-for="e in agentHistory" :key="e.id">
        <tr>
          <td class="text-xs" x-text="e.timestamp?.substring(0,19)"></td>
          <td x-text="e.agent"></td>
          <td class="text-xs" x-text="e.task?.substring(0,80)"></td>
          <td><span :class="'badge badge-'+(e.status==='completed'?'green':'red')" x-text="e.status"></span></td>
          <td x-text="e.tokens?.toLocaleString()"></td>
        </tr>
      </template>
      <template x-if="!agentHistory?.length">
        <tr><td colspan="5" class="text-muted text-center">No agent executions yet</td></tr>
      </template>
    </table>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Advanced
# ---------------------------------------------------------------------------
SECTION_ADVANCED = """
<section x-show="tab==='advanced'" x-cloak>
  <h2 class="mb-12">Advanced Tools</h2>
  <div class="grid-wide">
    <div class="card">
      <h3>Knowledge Graph Explorer</h3>
      <div class="form-row">
        <input type="text" x-model="kgQuery" placeholder="Entity name..." style="flex:1">
        <button class="btn btn-blue btn-sm" @click="queryKG()">Search</button>
      </div>
      <template x-if="kgResult">
        <div class="mt-8">
          <template x-if="kgResult.entity">
            <div>
              <div class="text-sm mb-4"><strong x-text="kgResult.entity.name"></strong>
                <span class="badge badge-blue" x-text="kgResult.entity.type"></span>
                <span class="text-muted text-xs" x-text="'mentions: '+kgResult.entity.mention_count"></span></div>
              <div class="text-xs text-muted" x-text="kgResult.entity.description"></div>
            </div>
          </template>
          <div class="mt-8" x-show="kgResult.outgoing?.length">
            <div class="text-xs text-muted mb-4">Outgoing relations:</div>
            <template x-for="r in kgResult.outgoing" :key="r.target+r.relation">
              <div class="text-sm">→ <span class="text-muted" x-text="r.relation"></span> → <strong x-text="r.target"></strong>
                <span class="text-xs text-muted" x-text="'(strength: '+r.strength?.toFixed(1)+')'"></span></div>
            </template>
          </div>
          <div class="mt-8" x-show="kgResult.incoming?.length">
            <div class="text-xs text-muted mb-4">Incoming relations:</div>
            <template x-for="r in kgResult.incoming" :key="r.source+r.relation">
              <div class="text-sm"><strong x-text="r.source"></strong> → <span class="text-muted" x-text="r.relation"></span> →
                <span class="text-xs text-muted" x-text="'(strength: '+r.strength?.toFixed(1)+')'"></span></div>
            </template>
          </div>
        </div>
      </template>
      <div class="mt-12">
        <div class="text-xs text-muted mb-4">KG Stats</div>
        <template x-if="analytics.kg">
          <div class="text-sm">
            <span x-text="analytics.kg.entity_count||0"></span> entities,
            <span x-text="analytics.kg.relation_count||0"></span> relations
          </div>
        </template>
      </div>
    </div>
    <div class="card">
      <h3>PII Scanner</h3>
      <textarea x-model="piiText" placeholder="Paste text to scan for PII..." rows="4"></textarea>
      <button class="btn btn-blue btn-sm mt-8" @click="scanPII()">Scan</button>
      <template x-if="piiResult">
        <div class="mt-8">
          <span class="badge" :class="piiResult.has_pii?'badge-red':'badge-green'" x-text="piiResult.has_pii?'PII detected':'Clean'"></span>
          <span class="text-sm text-muted" x-text="'('+piiResult.detection_count+' detections)'"></span>
          <template x-if="piiResult.detections?.length">
            <div class="mt-8">
              <template x-for="(d,i) in piiResult.detections" :key="i">
                <div class="text-sm mb-4"><span class="badge badge-yellow" x-text="d.type"></span>
                  <span class="mono text-xs" x-text="d.preview"></span></div>
              </template>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
  <div class="grid-wide mt-12">
    <div class="card">
      <h3>Pipeline Explainer</h3>
      <div class="form-row">
        <input type="text" x-model="explainQuery" placeholder="Query to explain..." style="flex:1">
        <button class="btn btn-blue btn-sm" @click="runExplain()">Explain</button>
      </div>
      <template x-if="explainResult">
        <div class="pre-wrap mt-8" x-text="JSON.stringify(explainResult,null,2)"></div>
      </template>
    </div>
    <div class="card">
      <h3>Working Memory</h3>
      <template x-if="analytics.wm">
        <div>
          <div class="text-sm">Active sessions: <strong x-text="analytics.wm.active_sessions||0"></strong></div>
          <div class="text-sm">Max sessions: <strong x-text="analytics.wm.max_sessions||0"></strong></div>
          <div class="text-sm">Total created: <strong x-text="analytics.wm.total_created||0"></strong></div>
        </div>
      </template>
      <button class="btn btn-muted btn-sm mt-8" @click="loadAnalytics()">Refresh</button>
    </div>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Scheduler
# ---------------------------------------------------------------------------
SECTION_SCHEDULER = """
<section x-show="tab==='scheduler'" x-cloak>
  <div x-data="schedulerSection()" x-init="init()">

    <!-- Header -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 style="font-size:18px;font-weight:700">Scheduler</h2>
      <button class="btn btn-blue" @click="showForm=true;editJob=null;resetForm()">+ Nouveau job</button>
    </div>

    <!-- Job list table -->
    <div class="card">
      <table class="tbl">
        <thead>
          <tr>
            <th>Nom</th><th>Prochain d&#233;clenchement</th><th>Canaux</th>
            <th>Dernier statut</th><th>Actif</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <template x-for="job in jobs" :key="job.id">
            <tr>
              <td x-text="job.name"></td>
              <td x-text="job.next_run_time ? relativeTime(job.next_run_time) : '\u2014'" class="mono"></td>
              <td><span x-text="(job.channels||[]).join(', ')" class="badge badge-blue"></span></td>
              <td>
                <span :class="{'badge-green':job.last_status==='ok','badge-red':job.last_status==='error','badge-yellow':job.last_status==='timeout','badge-muted':!job.last_status}" class="badge" x-text="job.last_status||'jamais'"></span>
              </td>
              <td>
                <input type="checkbox" :checked="job.enabled" @change="toggleJob(job, $event.target.checked)">
              </td>
              <td style="white-space:nowrap">
                <button class="btn btn-muted btn-sm" @click="openEdit(job)">Modifier</button>
                <button class="btn btn-blue btn-sm" @click="runNow(job)" :disabled="job.last_status==='running'">\u25b6 Lancer</button>
                <button class="btn btn-muted btn-sm" @click="openHistory(job)">Historique</button>
                <button class="btn btn-red btn-sm" @click="deleteJob(job)" x-show="!job.system">Supprimer</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Create/Edit form panel -->
    <div x-show="showForm" class="card" style="margin-top:12px">
      <h3 x-text="editJob ? 'Modifier le job' : 'Nouveau job'"></h3>
      <div class="form-row">
        <label>Nom</label>
        <input type="text" x-model="form.name" style="flex:1">
      </div>
      <div class="form-row">
        <label>Cron</label>
        <input type="text" x-model="form.cron" placeholder="0 8 * * *" style="flex:1">
        <span x-text="nextRunHint" class="mono" style="color:var(--muted);font-size:11px"></span>
      </div>
      <div class="form-row">
        <label>Timeout (s)</label>
        <input type="number" x-model.number="form.timeout_s" min="10" max="300" style="width:80px">
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;margin-bottom:4px;color:var(--muted);font-size:12px">SECTIONS</label>
        <template x-for="sec in allSections" :key="sec.key">
          <label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">
            <input type="checkbox" :value="sec.key" x-model="form.sections"> <span x-text="sec.label"></span>
          </label>
        </template>
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;margin-bottom:4px;color:var(--muted);font-size:12px">CANAUX</label>
        <template x-for="ch in allChannels" :key="ch">
          <label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">
            <input type="checkbox" :value="ch" x-model="form.channels"> <span x-text="ch"></span>
          </label>
        </template>
      </div>
      <div x-show="form.sections.includes('custom')" style="margin-bottom:8px">
        <label style="display:block;margin-bottom:4px;color:var(--muted);font-size:12px">PROMPT PERSONNALIS&#201;</label>
        <textarea x-model="form.prompt" rows="3" placeholder="Variables: {{date}} {{time}} {{hostname}} {{job_name}}"></textarea>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-blue" @click="saveJob()">Sauvegarder</button>
        <button class="btn btn-green" @click="testJob()" x-show="editJob">Tester maintenant</button>
        <button class="btn btn-muted" @click="showForm=false">Annuler</button>
      </div>
      <div x-show="testOutput" class="card" style="margin-top:8px;background:var(--input-bg)">
        <pre x-text="testOutput" style="white-space:pre-wrap;font-size:12px"></pre>
      </div>
    </div>

    <!-- History panel -->
    <div x-show="historyJob" class="card" style="margin-top:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <h3>Historique &mdash; <span x-text="historyJob?.name"></span></h3>
        <button class="btn btn-muted btn-sm" @click="historyJob=null">&#10005; Fermer</button>
      </div>
      <table class="tbl">
        <thead><tr><th>Date</th><th>Dur&#233;e</th><th>Statut</th><th>Canaux</th><th>Aper&#231;u</th></tr></thead>
        <tbody>
          <template x-for="run in history" :key="run.id">
            <tr>
              <td x-text="run.started_at" class="mono"></td>
              <td x-text="run.duration_ms ? run.duration_ms+'ms' : '\u2014'"></td>
              <td><span :class="{'badge-green':run.status==='ok','badge-red':run.status==='error','badge-yellow':run.status==='timeout'}" class="badge" x-text="run.status"></span></td>
              <td x-text="run.channels_ok ? JSON.stringify(JSON.parse(run.channels_ok)) : '\u2014'" class="mono" style="font-size:11px"></td>
              <td x-text="run.output ? run.output.slice(0,150)+'\u2026' : '\u2014'" style="font-size:12px"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------
ADMIN_JS = r"""
function adminApp(){return{
  // --- Nav ---
  tabs:[
    {id:'analytics',label:'Analytics'},{id:'settings',label:'Settings'},
    {id:'tools',label:'Tools'},{id:'vectordb',label:'Vector DB'},
    {id:'logs',label:'Logs'},{id:'chat',label:'Chat'},
    {id:'channels',label:'Channels'},{id:'shell',label:'Shell'},
    {id:'config',label:'Config'},{id:'trust',label:'Trust'},
    {id:'costs',label:'Costs'},{id:'workflows',label:'Workflows'},
    {id:'agents',label:'Agents'},{id:'advanced',label:'Advanced'},
    {id:'scheduler',label:'Scheduler'}
  ],
  tab:location.hash.slice(1)||'analytics',
  token:localStorage.getItem('bt')||'',

  // --- Analytics ---
  health:null,analytics:{cbs:null,cache:null,rates:null,feedback:null,ingest:null,profile:null,kg:null,wm:null,plugins:null,routes:null},
  autoRefresh:true,_refreshTimer:null,costChart:null,cacheChart:null,

  // --- Settings ---
  allSettings:[],settingSections:[],settingFilter:'',editingSetting:'',editingValue:'',settingsConfigWriterEnabled:false,
  get filteredSettings(){return this.settingFilter?this.allSettings.filter(s=>s.section===this.settingFilter):this.allSettings},

  // --- Tools ---
  plugins:{},routes:null,routePreviewTask:'',routePreviewResult:null,

  // --- Vector DB ---
  collections:[],searchQuery:'',searchLimit:5,searchResults:[],
  browseCollection:'',scrollPoints:[],scrollNextOffset:null,

  // --- Logs ---
  auditLogs:[],auditTotal:0,auditOffset:0,logMethodFilter:'',logPathFilter:'',

  // --- Chat ---
  chatMessages:[],chatInput:'',chatStreaming:false,chatStreamText:'',chatSources:[],
  chatSessionId:'',chatOpts:{auto_classify:true,enable_hyde:true,enable_citations:true,enable_self_critique:true},
  pipelineSteps:[],

  // --- Channels ---
  channelStatus:{},pendingPairings:[],approvedUsers:[],dmPolicy:'pairing',

  // --- Shell ---
  pendingActions:[],actionHistory:[],elevatedCommands:{},proposeCmd:'',proposeDesc:'',proposeResult:'',

  // --- Config ---
  pendingConfigs:[],configHistory:[],diffPreview:'',

  // --- Trust (v10) ---
  trustPolicies:[],trustAudit:[],

  // --- Costs (v10) ---
  costData:null,costByModel:[],

  // --- Workflows (v10) ---
  workflows:[],

  // --- Agents (v10) ---
  agentList:[],agentHistory:[],

  // --- Advanced ---
  kgQuery:'',kgResult:null,piiText:'',piiResult:null,explainQuery:'',explainResult:null,

  // === Lifecycle ===
  init(){
    if(!this.token){const t=prompt('Bridge token:');if(t){this.token=t;localStorage.setItem('bt',t)}}
    window.addEventListener('hashchange',()=>{this.tab=location.hash.slice(1)||'analytics';this.loadTab()});
    this.loadTab();
    this._refreshTimer=setInterval(()=>{if(this.autoRefresh&&this.tab==='analytics')this.loadAnalytics()},30000);
  },

  // === API helper ===
  async api(path,opts={}){
    const o={...opts,headers:{'X-Bridge-Token':this.token,'Content-Type':'application/json',...(opts.headers||{})}};
    if(opts.body)o.body=JSON.stringify(opts.body);
    const r=await fetch(path,o);
    if(r.status===401){this.token='';localStorage.removeItem('bt');const t=prompt('Bridge token:');if(t){this.token=t;localStorage.setItem('bt',t)}throw new Error('auth')}
    return r.json();
  },

  // === Tab loader ===
  async loadTab(){
    try{
      switch(this.tab){
        case'analytics':await this.loadAnalytics();break;
        case'settings':await this.loadSettings();break;
        case'tools':await this.loadTools();break;
        case'vectordb':await this.loadVectorDB();break;
        case'logs':await this.loadLogs();break;
        case'channels':await this.loadChannels();break;
        case'shell':await this.loadShell();break;
        case'config':await this.loadConfig();break;
        case'trust':await this.loadTrust();break;
        case'costs':await this.loadCosts();break;
        case'workflows':await this.loadWorkflows();break;
        case'agents':await this.loadAgents();break;
        case'advanced':if(!this.analytics.kg)await this.loadAnalytics();break;
      }
    }catch(e){console.error('loadTab error:',e)}
  },

  // === Analytics ===
  async loadAnalytics(){
    try{
      const[h,cb,ca,ra,fb,ig,pr,kg,ts,wm,pl,ro]=await Promise.all([
        this.api('/healthz'),this.api('/circuit-breakers'),this.api('/cache-stats'),
        this.api('/rate-limits'),this.api('/feedback-stats'),this.api('/ingest-status'),
        this.api('/profile'),this.api('/knowledge-graph/stats'),this.api('/token-stats'),
        this.api('/working-memory'),this.api('/plugins'),this.api('/routes')]);
      this.health=h;this.analytics={cbs:cb,cache:ca,rates:ra,feedback:fb,ingest:ig,profile:pr,kg:kg,wm:wm,plugins:pl,routes:ro};
      this.routes=ro;
      this.$nextTick(()=>this.updateCharts(ts,ca));
    }catch(e){console.error('loadAnalytics:',e)}
  },

  updateCharts(tokenStats,cacheStats){
    // Cost chart
    const ctx1=document.getElementById('costChart');
    if(ctx1&&tokenStats?.by_model){
      if(this.costChart)this.costChart.destroy();
      const models=Object.keys(tokenStats.by_model);
      const costs=models.map(m=>tokenStats.by_model[m].cost||0);
      const calls=models.map(m=>tokenStats.by_model[m].calls||0);
      this.costChart=new Chart(ctx1,{type:'bar',data:{labels:models,datasets:[
        {label:'Cost ($)',data:costs,backgroundColor:'rgba(59,130,246,.6)',yAxisID:'y'},
        {label:'Calls',data:calls,backgroundColor:'rgba(139,92,246,.6)',yAxisID:'y1'}]},
        options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#888'}}},
        scales:{y:{position:'left',ticks:{color:'#888'},grid:{color:'#2a2d3a'}},
        y1:{position:'right',ticks:{color:'#888'},grid:{drawOnChartArea:false}}}}});
    }
    // Cache chart
    const ctx2=document.getElementById('cacheChart');
    if(ctx2&&cacheStats){
      if(this.cacheChart)this.cacheChart.destroy();
      this.cacheChart=new Chart(ctx2,{type:'doughnut',data:{
        labels:['Embed Hits','Embed Misses','LLM Hits','LLM Misses'],
        datasets:[{data:[cacheStats.embedding?.hits||0,cacheStats.embedding?.misses||0,
          cacheStats.llm?.hits||0,cacheStats.llm?.misses||0],
          backgroundColor:['#22c55e','#ef4444','#3b82f6','#eab308']}]},
        options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#888'}}}}});
    }
  },

  // === Settings ===
  async loadSettings(){
    try{
      const d=await this.api('/settings/sections');
      const sections=d.sections||{};
      this.settingSections=Object.keys(sections);
      this.allSettings=Object.values(sections).flat();
      const cw=this.allSettings.find(s=>s.key==='CONFIG_WRITER_ENABLED');
      this.settingsConfigWriterEnabled=cw&&cw.value==='true';
    }catch(e){console.error('loadSettings:',e)}
  },
  async proposeSetting(key){
    try{
      const r=await this.api('/settings/key/'+key,{method:'POST',body:{value:this.editingValue,description:'Changed via admin UI'}});
      this.editingSetting='';alert(r.ok?'Change proposed (ID: '+r.change_id+')':'Error: '+(r.error||JSON.stringify(r)));
      await this.loadSettings();
    }catch(e){alert('Error: '+e.message)}
  },

  // === Tools ===
  async loadTools(){
    try{
      this.plugins=await this.api('/plugins');
      this.routes=await this.api('/routes');
    }catch(e){console.error('loadTools:',e)}
  },
  async previewRoute(){
    if(!this.routePreviewTask)return;
    try{this.routePreviewResult=await this.api('/route-preview',{method:'POST',body:{task_type:this.routePreviewTask}})}catch(e){console.error(e)}
  },

  // === Vector DB ===
  async loadVectorDB(){
    try{const d=await this.api('/admin/collections');this.collections=d.collections||[]}catch(e){console.error(e)}
  },
  async runSearch(){
    if(!this.searchQuery)return;
    try{const d=await this.api('/search',{method:'POST',body:{query:this.searchQuery,limit:this.searchLimit}});
      this.searchResults=d.results||[]}catch(e){console.error(e)}
  },
  async scrollCollection(){
    try{const d=await this.api('/admin/collections/'+this.browseCollection+'/scroll',{method:'POST',body:{}});
      if(this.scrollNextOffset===null)this.scrollPoints=d.points||[];
      else this.scrollPoints=[...this.scrollPoints,...(d.points||[])];
      this.scrollNextOffset=d.next_offset}catch(e){console.error(e)}
  },

  // === Logs ===
  async loadLogs(){
    try{
      let url='/admin/audit-log?limit=100&offset='+this.auditOffset;
      if(this.logMethodFilter)url+='&method='+this.logMethodFilter;
      if(this.logPathFilter)url+='&path_filter='+encodeURIComponent(this.logPathFilter);
      const d=await this.api(url);
      this.auditLogs=d.entries||[];this.auditTotal=d.total||0;
    }catch(e){console.error(e)}
  },

  // === Chat ===
  async sendChat(){
    if(!this.chatInput.trim()||this.chatStreaming)return;
    const text=this.chatInput.trim();this.chatInput='';
    this.chatMessages.push({role:'user',content:text});
    this.chatStreaming=true;this.chatStreamText='';this.chatSources=[];
    this.pipelineSteps=[{name:'classify',status:''},{name:'sentiment',status:''},{name:'hyde',status:''},
      {name:'compress',status:''},{name:'retrieve',status:''},{name:'generate',status:''},{name:'critique',status:''}];
    try{
      const msgs=this.chatMessages.map(m=>({role:m.role,content:m.content}));
      const body={messages:msgs,session_id:this.chatSessionId||undefined,...this.chatOpts};
      const resp=await fetch('/smart-chat-stream',{method:'POST',
        headers:{'X-Bridge-Token':this.token,'Content-Type':'application/json'},body:JSON.stringify(body)});
      const reader=resp.body.getReader();const decoder=new TextDecoder();let buf='';
      while(true){
        const{done,value}=await reader.read();
        if(done)break;
        buf+=decoder.decode(value,{stream:true});
        const lines=buf.split('\n');buf=lines.pop()||'';
        let evtName='',evtData='';
        for(const line of lines){
          if(line.startsWith('event: '))evtName=line.slice(7).trim();
          else if(line.startsWith('data: ')){evtData=line.slice(6);
            try{const d=JSON.parse(evtData);this.handleSSE(evtName,d)}catch(e){}}
          else if(line==='')evtName='';
        }
      }
    }catch(e){console.error('chat error:',e)}
    if(this.chatStreamText)this.chatMessages.push({role:'assistant',content:this.chatStreamText});
    this.chatStreaming=false;
    this.$nextTick(()=>{const el=document.getElementById('chatMsgs');if(el)el.scrollTop=el.scrollHeight});
  },
  handleSSE(evt,data){
    if(evt==='progress'){
      const phase=data.phase||data.status||'';
      const step=this.pipelineSteps.find(s=>phase.toLowerCase().includes(s.name));
      if(step){this.pipelineSteps.forEach(s=>{if(s.status==='active')s.status='done'});step.status='active'}
    }else if(evt==='answer'){
      this.chatStreamText=data.text||'';
      if(data.sources)this.chatSources=data.sources;
      this.pipelineSteps.forEach(s=>{if(s.status)s.status='done'});
    }else if(evt==='done'){this.pipelineSteps.forEach(s=>{if(s.status)s.status='done'})}
  },
  renderMd(text){if(!text)return'';return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/`(.*?)`/g,'<code>$1</code>')
    .replace(/\n/g,'<br>')},

  // === Channels ===
  async loadChannels(){
    try{
      this.channelStatus=(await this.api('/channels/status'))||{};
      const pr=await this.api('/channels/pair/pending');this.pendingPairings=pr.pending||[];this.dmPolicy=pr.policy||'pairing';
      const ur=await this.api('/channels/pair/users');this.approvedUsers=ur.users||[];
    }catch(e){console.error(e)}
  },
  async approvePairing(code){try{await this.api('/channels/pair/'+code+'/approve',{method:'POST',body:{}});await this.loadChannels()}catch(e){alert('Error: '+e.message)}},
  async rejectPairing(code){try{await this.api('/channels/pair/'+code+'/reject',{method:'POST',body:{}});await this.loadChannels()}catch(e){alert('Error: '+e.message)}},
  async revokeUser(pid){if(!confirm('Revoke access for '+pid+'?'))return;
    try{await this.api('/channels/pair/revoke',{method:'POST',body:{platform_id:pid}});await this.loadChannels()}catch(e){alert('Error: '+e.message)}},

  // === Shell ===
  async loadShell(){
    try{
      this.pendingActions=await this.api('/actions/pending');
      this.actionHistory=await this.api('/actions/history');
      const cmds=await this.api('/actions/commands/list');this.elevatedCommands=cmds.commands||{};
    }catch(e){console.error(e)}
  },
  async proposeAction(){
    if(!this.proposeCmd)return;this.proposeResult='';
    try{const r=await this.api('/actions/propose',{method:'POST',body:{command:this.proposeCmd,description:this.proposeDesc}});
      this.proposeResult=r.ok?'Proposed (ID: '+r.action_id+')':'Error: '+r.error;
      this.proposeCmd='';this.proposeDesc='';await this.loadShell()
    }catch(e){this.proposeResult='Error: '+e.message}
  },
  async approveAction(id){try{await this.api('/actions/'+id+'/approve',{method:'POST',body:{}});await this.loadShell()}catch(e){alert('Error: '+e.message)}},
  async rejectAction(id){try{await this.api('/actions/'+id+'/reject',{method:'POST',body:{}});await this.loadShell()}catch(e){alert('Error: '+e.message)}},

  // === Config ===
  async loadConfig(){
    try{
      this.pendingConfigs=await this.api('/config/pending');
      this.configHistory=await this.api('/config/history');
    }catch(e){console.error(e)}
  },
  async previewDiff(id){try{const d=await this.api('/config/'+id+'/preview');this.diffPreview=d.diff||'(no diff)'}catch(e){console.error(e)}},
  async applyConfig(id){try{await this.api('/config/'+id+'/apply',{method:'POST',body:{}});await this.loadConfig()}catch(e){alert('Error: '+e.message)}},
  async rejectConfig(id){try{await this.api('/config/'+id+'/reject',{method:'POST',body:{}});await this.loadConfig()}catch(e){alert('Error: '+e.message)}},
  async rollbackConfig(id){if(!confirm('Rollback this change?'))return;
    try{await this.api('/config/'+id+'/rollback',{method:'POST',body:{}});await this.loadConfig()}catch(e){alert('Error: '+e.message)}},

  // === Advanced ===
  async queryKG(){if(!this.kgQuery)return;
    try{this.kgResult=await this.api('/knowledge-graph/query',{method:'POST',body:{entity:this.kgQuery}})}catch(e){console.error(e)}},
  async scanPII(){if(!this.piiText)return;
    try{this.piiResult=await this.api('/pii-check',{method:'POST',body:{text:this.piiText}})}catch(e){console.error(e)}},
  async runExplain(){if(!this.explainQuery)return;
    try{this.explainResult=await this.api('/explain',{method:'POST',body:{query:this.explainQuery}})}catch(e){console.error(e)}},

  // === Trust (v10) ===
  async loadTrust(){
    try{const[p,a]=await Promise.all([this.api('/trust/policies'),this.api('/trust/audit?limit=50')]);
      this.trustPolicies=p.policies||[];this.trustAudit=a.entries||[]}catch(e){console.error('loadTrust:',e)}},
  async updateTrust(type,level){
    try{await this.api('/trust/policies/'+type,{method:'POST',body:{trust_level:level}});await this.loadTrust()}catch(e){alert('Error: '+e.message)}},
  async promoteTrust(type){
    try{await this.api('/trust/promote/'+type,{method:'POST',body:{}});await this.loadTrust()}catch(e){alert('Error: '+e.message)}},

  // === Costs (v10) ===
  async loadCosts(){
    try{const[b,r]=await Promise.all([this.api('/budget/status'),this.api('/budget/daily-report')]);
      this.costData=b;this.costByModel=r.by_model||[]}catch(e){console.error('loadCosts:',e)}},

  // === Workflows (v10) ===
  async loadWorkflows(){
    try{const d=await this.api('/workflows');this.workflows=d.workflows||[]}catch(e){console.error('loadWorkflows:',e)}},
  async toggleWorkflow(id,enabled){
    try{await this.api('/workflows/'+id+'/toggle',{method:'POST',body:{auto_suggest:enabled}});await this.loadWorkflows()}catch(e){alert('Error: '+e.message)}},

  // === Agents (v10) ===
  async loadAgents(){
    try{const[s,h]=await Promise.all([this.api('/agent/status'),this.api('/agent/history')]);
      this.agentList=s.agents||[];this.agentHistory=h.executions||[]}catch(e){console.error('loadAgents:',e)}}
}}

function schedulerSection(){
  return{
    jobs:[],showForm:false,editJob:null,historyJob:null,history:[],
    testOutput:'',
    form:{name:'',cron:'0 8 * * *',sections:[],channels:[],prompt:'',timeout_s:60},
    allSections:[
      {key:'system_health',label:'Sant\u00e9 syst\u00e8me'},
      {key:'personal_notes',label:'Notes r\u00e9centes'},
      {key:'topics',label:'Sujets (\u26a0 co\u00fbt LLM)'},
      {key:'reminders',label:'Rappels'},
      {key:'weekly_summary',label:'Bilan hebdo'},
      {key:'custom',label:'Prompt personnalis\u00e9'},
    ],
    allChannels:['ntfy','telegram','discord','whatsapp'],
    get nextRunHint(){
      try{return this.form.cron?'cron: '+this.form.cron:'';}catch(e){return '';}
    },
    async init(){await this.loadJobs();},
    async loadJobs(){
      const r=await fetch('/api/scheduler/jobs');
      if(r.ok)this.jobs=await r.json();
    },
    resetForm(){
      this.form={name:'',cron:'0 8 * * *',sections:[],channels:[],prompt:'',timeout_s:60};
      this.testOutput='';
    },
    openEdit(job){
      this.editJob=job;
      this.form={name:job.name,cron:job.cron,sections:[...job.sections],
                 channels:[...job.channels],prompt:job.prompt||'',timeout_s:job.timeout_s};
      this.showForm=true;
    },
    async saveJob(){
      const url=this.editJob?'/api/scheduler/jobs/'+this.editJob.id:'/api/scheduler/jobs';
      const method=this.editJob?'PUT':'POST';
      const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},
                               body:JSON.stringify(this.form)});
      if(r.ok){this.showForm=false;await this.loadJobs();}
      else{const e=await r.json();alert(e.detail||'Erreur');}
    },
    async testJob(){
      this.testOutput='Ex\u00e9cution en cours\u2026';
      const r=await fetch('/api/scheduler/jobs/'+this.editJob.id+'/run',{method:'POST'});
      if(r.ok){
        this.testOutput='Job d\u00e9clench\u00e9. R\u00e9sultat visible dans l\'historique dans quelques secondes.';
        setTimeout(()=>this.loadJobs(),3000);
      }else{
        const e=await r.json();
        this.testOutput='Erreur: '+(e.detail||JSON.stringify(e));
      }
    },
    async runNow(job){
      const r=await fetch('/api/scheduler/jobs/'+job.id+'/run',{method:'POST'});
      if(r.ok){job.last_status='running';}
      else{const e=await r.json();alert(e.detail||'Erreur');}
    },
    async toggleJob(job,enabled){
      await fetch('/api/scheduler/jobs/'+job.id+'/toggle?enabled='+enabled,{method:'POST'});
      await this.loadJobs();
    },
    async deleteJob(job){
      if(!confirm('Supprimer "'+job.name+'" ?'))return;
      const r=await fetch('/api/scheduler/jobs/'+job.id,{method:'DELETE'});
      if(r.ok)await this.loadJobs();
      else{const e=await r.json();alert(e.detail||'Erreur');}
    },
    async openHistory(job){
      this.historyJob=job;
      const r=await fetch('/api/scheduler/jobs/'+job.id+'/history');
      if(r.ok)this.history=await r.json();
    },
    relativeTime(iso){
      const diff=new Date(iso)-new Date();
      const abs=Math.abs(diff);
      if(abs<60000)return'maintenant';
      if(abs<3600000)return'dans '+Math.round(abs/60000)+'min';
      if(abs<86400000)return'dans '+Math.round(abs/3600000)+'h';
      return'dans '+Math.round(abs/86400000)+'j';
    },
  };
}
"""

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def build_admin_html() -> str:
    """Assemble the full admin SPA HTML."""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>nanobot admin</title>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>{ADMIN_CSS}</style>
</head>
<body x-data="adminApp()" x-init="init()">
{ADMIN_NAV}
<main>
{SECTION_ANALYTICS}
{SECTION_SETTINGS}
{SECTION_TOOLS}
{SECTION_VECTOR_DB}
{SECTION_LOGS}
{SECTION_CHAT}
{SECTION_CHANNELS}
{SECTION_SHELL}
{SECTION_CONFIG}
{SECTION_TRUST}
{SECTION_COSTS}
{SECTION_WORKFLOWS}
{SECTION_AGENTS}
{SECTION_ADVANCED}
{SECTION_SCHEDULER}
</main>
<script>{ADMIN_JS}</script>
</body></html>"""
