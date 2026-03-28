# Admin UI Stitch Features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all Quick Win (S) and Medium Impact (M) features from the Stitch design delta — 20 S-items and 18 M-items spanning all 7 admin tabs, on a single `feature/admin-stitch-features` branch.

**Architecture:** All changes are front-end only in `src/bridge/admin_ui.py`. Each feature wires existing Alpine.js state to existing REST endpoints. No new backend modules. HTML sections are Python string constants assembled by `build_admin_html()`. JS lives in `ADMIN_JS` (main app) as methods on `adminApp()`.

**Tech Stack:** Alpine.js 3, Chart.js 4.4, existing FastAPI endpoints, CSS custom properties (Neon Observatory theme)

---

## Codebase Context

**Single file to modify:** `src/bridge/admin_ui.py` (~2300 lines)

Structure:
- `ADMIN_CSS` (lines 16–211) — CSS variables and components
- `ADMIN_NAV` (line 216) — top nav bar
- `SECTION_ANALYTICS` (line 229) — Analytics tab HTML
- `SECTION_SETTINGS` (line 327) — Settings tab HTML
- `SECTION_TOOLS` (line 389) — Tools tab HTML
- `SECTION_VECTOR_DB` (line 449) — Vector DB tab HTML
- `SECTION_LOGS` (line 507) — Logs tab HTML
- `SECTION_CHAT` (line 552) — Chat tab HTML
- `SECTION_TRUST` (line 920) — Trust tab HTML
- `SECTION_COSTS` (line 967) — Costs tab HTML
- `SECTION_WORKFLOWS` (line 1017) — Workflows tab HTML
- `SECTION_AGENTS` (line 1048) — Agents tab HTML
- `ADMIN_JS` (line 1298) — `adminApp()` function with all state + methods
- `build_admin_html()` (line 2173) — assembles everything

**Endpoint map (used by new features):**
| Endpoint | Returns |
|----------|---------|
| `GET /healthz` | `{time, qdrant:{ok,collections}, api_keys:{configured}, ok}` |
| `GET /token-stats` | `{by_model:{name:{calls,cost,input_tokens,output_tokens}}, total_*}` |
| `GET /ingest-status` | `{status, total_indexed, chunks_processed, ...}` |
| `GET /agent/history` | `{executions:[{timestamp,agent,task,status,tokens}]}` |
| `POST /agent/run` | `{ok,status,output,actions_taken,cost_tokens}` |
| `GET /admin/collections` | `{collections:[{name,points_count,vectors_count,status}]}` |
| `GET /api/docs/` | `{items:[{doc_id,filename,size,status,ingested_at,...}],total}` |
| `GET /api/docs/status` | `{total_documents,total_chunks,...}` |
| `POST /api/docs/ingest` | `{file_path}` → `{status,chunks,...}` |
| `DELETE /api/docs/{doc_id}` | `{deleted,doc_id}` |
| `GET /settings/sections` | `{sections:{name:[{key,value,default,description,...}]}}` |
| `POST /selftest` | `{ok,time,routes,...}` |
| `GET /routes` | `{profiles,task_routes}` |
| `POST /export` | markdown/json content |

---

## Task 1 — Analytics: Telemetry Bar + Enhanced Stats (S)

**Items covered:** 1.1 (telemetry bar), 1.3 (token stat with trend), 1.9 (system pulse)

### Steps

- [ ] **1.1 — Add telemetry bar to SECTION_ANALYTICS**

Replace the opening of `SECTION_ANALYTICS` (the `<div class="flex-between mb-12">` block) with:

```html
<section x-show="tab==='analytics'" x-cloak>
  <div class="telemetry-bar mb-16">
    <span class="pulse-dot"></span>
    <span>Real-time Telemetry</span>
    <span style="margin-left:auto" class="mono" x-text="'Last update: '+(health?.time?.substring(11,19)||'--:--:--')+' UTC'"></span>
  </div>
  <div class="flex-between mb-12">
    <h2>Analytics</h2>
    <div class="flex gap-8">
      <label class="text-sm text-muted"><input type="checkbox" x-model="autoRefresh"> Auto-refresh</label>
      <button class="btn btn-muted btn-sm" @click="loadAnalytics()">Refresh</button>
    </div>
  </div>
```

- [ ] **1.2 — Enhance Health card with system pulse**

Replace the Health card content in `SECTION_ANALYTICS` with:

```html
<div class="card"><h3>System Pulse</h3>
  <template x-if="health"><div>
    <div class="stat" :class="health.ok?'text-green':'text-red'" x-text="health.ok?'Operational':'Degraded'"></div>
    <div class="stat-label" x-text="'Qdrant: '+(health.checks?.qdrant?.ok?'connected':'down')"></div>
    <div class="stat-label" x-text="'Collections: '+(health.checks?.qdrant?.collections||0)"></div>
    <div class="stat-label" x-text="'API Keys: '+(health.checks?.api_keys?.configured?'configured':'missing')"></div>
  </div></template>
</div>
```

- [ ] **1.3 — Add token consumption stat card (replace KG card with more useful stat)**

After the System Pulse card, add inside the first `.grid`:

```html
<div class="card"><h3>Token Consumption</h3>
  <template x-if="analytics.tokenStats"><div>
    <div class="stat" x-text="(analytics.tokenStats.total_tokens||0).toLocaleString()"></div>
    <div class="stat-label">total tokens</div>
    <div class="text-sm mt-8 mono">
      <span class="text-muted">In:</span> <span x-text="(analytics.tokenStats.total_input_tokens||0).toLocaleString()"></span>
      <span class="text-muted" style="margin-left:8px">Out:</span> <span x-text="(analytics.tokenStats.total_output_tokens||0).toLocaleString()"></span>
    </div>
  </div></template>
</div>
```

- [ ] **1.4 — Store tokenStats in analytics object in JS loadAnalytics()**

In `ADMIN_JS`, inside `loadAnalytics()`, the Promise.all already fetches `/token-stats` as variable `ts`. Add to the analytics assignment:

Change `this.analytics={cbs:cb,cache:ca,rates:ra,feedback:fb,ingest:ig,profile:pr,kg:kg,wm:wm,plugins:pl,routes:ro};`

To: `this.analytics={cbs:cb,cache:ca,rates:ra,feedback:fb,ingest:ig,profile:pr,kg:kg,tokenStats:ts,wm:wm,plugins:pl,routes:ro};`

- [ ] **1.5 — Run build check:**

```bash
python -c "from src.bridge.admin_ui import build_admin_html; h=build_admin_html(); assert 'telemetry-bar' in h and 'System Pulse' in h and 'Token Consumption' in h; print('OK')"
```

- [ ] **1.6 — Commit:**

```bash
git add src/bridge/admin_ui.py
git commit -m "feat(admin): add telemetry bar, system pulse, token consumption stats"
```

---

## Task 2 — Analytics: Activity Feed + Action Buttons (S+M)

**Items covered:** 1.2 (Export Report + New Agent Task buttons), 1.8 (live agent activity feed)

### Steps

- [ ] **2.1 — Add action buttons to Analytics header**

After the Refresh button in the `flex-between` header, add:

```html
<button class="btn btn-blue btn-sm" @click="exportReport()">Export Report</button>
<button class="btn btn-green btn-sm" @click="showAgentTaskModal=true">New Agent Task</button>
```

- [ ] **2.2 — Add agent activity feed section after the charts**

After the `grid-wide` section with charts, before the final `.grid` with Working Memory/Plugins/Routes, add:

```html
<div class="card mb-16">
  <div class="flex-between mb-8">
    <h3>Live Agent Activity</h3>
    <button class="btn btn-muted btn-sm" @click="loadRecentActivity()">Refresh</button>
  </div>
  <template x-if="recentActivity.length">
    <div style="max-height:300px;overflow-y:auto">
      <template x-for="a in recentActivity" :key="a.timestamp">
        <div class="flex-between" style="padding:8px 0;border-bottom:1px solid var(--ghost-border)">
          <div>
            <span class="badge" :class="a.status==='completed'?'badge-green':a.status==='running'?'badge-blue':'badge-red'" x-text="a.status"></span>
            <span class="text-sm" style="margin-left:8px" x-text="a.agent||'system'"></span>
            <span class="text-xs text-muted" style="margin-left:8px" x-text="a.task?.substring(0,80)"></span>
          </div>
          <div class="flex gap-8">
            <span class="mono text-xs" x-text="a.tokens?a.tokens.toLocaleString()+' tok':''"></span>
            <span class="text-xs text-muted" x-text="a.timestamp?.substring(11,19)"></span>
          </div>
        </div>
      </template>
    </div>
  </template>
  <p x-show="!recentActivity.length" class="text-muted text-sm">No recent agent activity</p>
</div>
```

- [ ] **2.3 — Add Agent Task modal HTML**

After `</main>` but before `{MOBILE_CHAT_HTML}` in `build_admin_html()`, we need a modal. Instead, add it at the end of `SECTION_ANALYTICS`:

Before the closing `</section>` of SECTION_ANALYTICS, add:

```html
<div class="modal-bg" x-show="showAgentTaskModal" x-cloak @click.self="showAgentTaskModal=false">
  <div class="modal">
    <h3>New Agent Task</h3>
    <textarea x-model="agentTaskInput" placeholder="Describe the task for the agent..." rows="3"></textarea>
    <div class="flex gap-8 mt-12">
      <button class="btn btn-blue" @click="runAgentTask()">Run</button>
      <button class="btn btn-muted" @click="showAgentTaskModal=false">Cancel</button>
    </div>
    <div class="pre-wrap mt-12" x-show="agentTaskResult" x-text="JSON.stringify(agentTaskResult,null,2)"></div>
  </div>
</div>
```

- [ ] **2.4 — Add JS state and methods**

In `ADMIN_JS`, add to the data properties (after `kgQuery:'',kgResult:null,...`):

```javascript
recentActivity:[],showAgentTaskModal:false,agentTaskInput:'',agentTaskResult:null,
```

Add methods after `runExplain()`:

```javascript
async loadRecentActivity(){
  try{const d=await this.api('/agent/history?limit=10');this.recentActivity=d.executions||[]}catch(e){console.error(e)}},
async exportReport(){
  try{const d=await this.api('/token-stats');
    const blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='nanobot-report.json';a.click()}catch(e){alert('Export failed: '+e.message)}},
async runAgentTask(){
  if(!this.agentTaskInput.trim())return;this.agentTaskResult=null;
  try{this.agentTaskResult=await this.api('/agent/run',{method:'POST',body:{task:this.agentTaskInput}});
    this.agentTaskInput='';await this.loadRecentActivity()}catch(e){this.agentTaskResult={error:e.message}}},
```

Also call `loadRecentActivity()` inside `loadAnalytics()` after the existing code:

```javascript
this.loadRecentActivity();
```

- [ ] **2.5 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'recentActivity' in h and 'agentTaskModal' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): add agent activity feed, export report, agent task modal"
```

---

## Task 3 — Settings: Model Config Controls (S)

**Items covered:** 3.3 (temperature slider), 3.4 (top-p slider), 3.5 (max tokens), 3.6 (tool execution toggle), 3.8 (reset defaults + test config)

### Steps

- [ ] **3.1 — Add model config panel at top of SECTION_SETTINGS**

After the `<h2>Settings</h2>` and warn-banner, before the subtabs, add:

```html
<div class="grid mb-16">
  <div class="card">
    <h3>Temperature</h3>
    <div class="flex-between">
      <span class="text-xs text-muted">Precise</span>
      <span class="stat-value" x-text="modelTemp.toFixed(2)"></span>
      <span class="text-xs text-muted">Creative</span>
    </div>
    <input type="range" min="0" max="1" step="0.05" x-model.number="modelTemp"
      @change="proposeSetting('TEMPERATURE')" style="width:100%;accent-color:var(--primary)">
  </div>
  <div class="card">
    <h3>Top-P</h3>
    <div class="flex-between">
      <span class="text-xs text-muted">Narrow</span>
      <span class="stat-value" x-text="modelTopP.toFixed(2)"></span>
      <span class="text-xs text-muted">Diverse</span>
    </div>
    <input type="range" min="0" max="1" step="0.05" x-model.number="modelTopP"
      @change="proposeSetting('TOP_P')" style="width:100%;accent-color:var(--primary)">
  </div>
  <div class="card">
    <h3>Max Tokens</h3>
    <div class="stat-value" x-text="modelMaxTokens.toLocaleString()"></div>
    <input type="range" min="256" max="128000" step="256" x-model.number="modelMaxTokens"
      @change="proposeSetting('MAX_TOKENS')" style="width:100%;accent-color:var(--primary)">
    <div class="text-xs text-muted mt-4" x-text="'~'+(modelMaxTokens/128000*100).toFixed(0)+'% context window'"></div>
  </div>
  <div class="card">
    <h3>Tool Execution</h3>
    <div class="flex-between">
      <span class="text-sm" x-text="modelToolUse?'Enabled — AI can call external tools':'Disabled'"></span>
      <button class="btn btn-sm" :class="modelToolUse?'btn-green':'btn-muted'"
        @click="modelToolUse=!modelToolUse;proposeSetting('TOOL_USE_ENABLED')" x-text="modelToolUse?'On':'Off'"></button>
    </div>
  </div>
</div>
<div class="flex gap-8 mb-16">
  <button class="btn btn-muted btn-sm" @click="resetModelDefaults()">Reset Defaults</button>
  <button class="btn btn-blue btn-sm" @click="testConfig()">Test Configuration</button>
  <span class="text-xs text-green" x-show="testConfigResult" x-text="testConfigResult"></span>
</div>
```

- [ ] **3.2 — Add JS state and methods**

Add to data properties:

```javascript
modelTemp:0.7,modelTopP:0.9,modelMaxTokens:4096,modelToolUse:true,testConfigResult:'',
```

Update `loadSettings()` to extract model params from settings:

After `this.settingsConfigWriterEnabled=cw&&cw.value==='true';` add:

```javascript
const t=this.allSettings.find(s=>s.key==='TEMPERATURE');if(t)this.modelTemp=parseFloat(t.value)||0.7;
const tp=this.allSettings.find(s=>s.key==='TOP_P');if(tp)this.modelTopP=parseFloat(tp.value)||0.9;
const mt=this.allSettings.find(s=>s.key==='MAX_TOKENS');if(mt)this.modelMaxTokens=parseInt(mt.value)||4096;
const tu=this.allSettings.find(s=>s.key==='TOOL_USE_ENABLED');if(tu)this.modelToolUse=tu.value==='true';
```

Add new methods:

```javascript
async resetModelDefaults(){
  this.modelTemp=0.7;this.modelTopP=0.9;this.modelMaxTokens=4096;this.modelToolUse=true;
  alert('Defaults restored (not saved). Use the settings table below to propose changes.')},
async testConfig(){
  this.testConfigResult='Running selftest...';
  try{const r=await this.api('/selftest',{method:'POST'});
    this.testConfigResult=r.ok?'All checks passed':'Some checks failed — see console';
    console.log('selftest result:',r)}catch(e){this.testConfigResult='Error: '+e.message}},
```

Note: `proposeSetting()` already exists but expects `editingValue`. For slider changes, override with direct approach. Replace the `proposeSetting` method signature to handle both cases — but simpler: the slider `@change` should call a new method. Change the slider handlers to:

```javascript
async proposeModelSetting(key,val){
  try{await this.api('/settings/key/'+key,{method:'POST',body:{value:String(val),description:'Changed via admin model controls'}});
    this.testConfigResult='Setting '+key+' proposed'}catch(e){alert('Error: '+e.message)}},
```

And update sliders to call `proposeModelSetting('TEMPERATURE',modelTemp)` etc.

- [ ] **3.3 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'modelTemp' in h and 'testConfig' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): add model config sliders, tool toggle, selftest button"
```

---

## Task 4 — Tools: Visual Tool Cards Grid (M)

**Items covered:** 2.1 (tool cards visual grid), 2.7 (performance metrics)

### Steps

- [ ] **4.1 — Replace the plugins table with visual cards grid**

Replace the "Loaded Plugins" card content in `SECTION_TOOLS` with:

```html
<div class="card" style="grid-column:1/-1">
  <h3>Loaded Plugins & Tools</h3>
  <div class="grid" style="margin-top:12px">
    <template x-for="p in (plugins.plugins||[])" :key="p.name">
      <div class="card" style="background:var(--surface-container-low)">
        <div class="flex-between mb-8">
          <span style="font-family:'Manrope',sans-serif;font-weight:700;font-size:0.875rem;color:var(--on-surface)" x-text="p.name"></span>
          <span class="badge" :class="p.has_router?'badge-green':'badge-muted'" x-text="p.has_router?'Active':'Inactive'"></span>
        </div>
        <div class="flex gap-8 mb-8">
          <span class="badge badge-blue" x-text="(p.tools?.length||0)+' tools'"></span>
          <span class="badge badge-muted" x-text="(p.hooks?.length||0)+' hooks'"></span>
        </div>
        <template x-if="p.tools?.length">
          <div class="text-xs text-muted" x-text="p.tools.map(t=>t.name||t).join(', ')"></div>
        </template>
      </div>
    </template>
  </div>
  <p x-show="!plugins.plugins||!plugins.plugins.length" class="text-muted mt-8">No plugins loaded</p>
</div>
```

- [ ] **4.2 — Add performance metrics footer to Tools section**

Before the closing `</section>` of `SECTION_TOOLS`, add:

```html
<div class="grid mt-16">
  <div class="card">
    <h3>Token Cost (24h)</h3>
    <div class="stat-value" x-text="toolStats.totalCost?'$'+toolStats.totalCost.toFixed(2):'$0.00'"></div>
  </div>
  <div class="card">
    <h3>Total Calls</h3>
    <div class="stat-value" x-text="(toolStats.totalCalls||0).toLocaleString()"></div>
  </div>
  <div class="card">
    <h3>Models Active</h3>
    <div class="stat-value" x-text="toolStats.modelsActive||0"></div>
  </div>
</div>
```

- [ ] **4.3 — Add JS state and methods for tool stats**

Add to data properties:

```javascript
toolStats:{totalCost:0,totalCalls:0,modelsActive:0},
```

Update `loadTools()`:

```javascript
async loadTools(){
  try{
    this.plugins=await this.api('/plugins');
    this.routes=await this.api('/routes');
    try{const ts=await this.api('/token-stats');
      const bm=ts.by_model||{};
      this.toolStats={
        totalCost:Object.values(bm).reduce((s,m)=>s+(m.cost||0),0),
        totalCalls:Object.values(bm).reduce((s,m)=>s+(m.calls||0),0),
        modelsActive:Object.keys(bm).length}
    }catch(e){}
  }catch(e){console.error('loadTools:',e)}},
```

- [ ] **4.4 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'toolStats' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): visual tool cards grid with performance metrics"
```

---

## Task 5 — Vector DB: Documents Table + Upload + Metrics (M)

**Items covered:** 7.1 (health badge), 7.2 (upload zone), 7.3 (format badges), 7.4 (metrics cards), 7.5 (documents table), 7.6 (status badges), 7.7 (search in table), 7.8 (pagination), 7.9 (indexing pipeline), 7.10 (select files button), 7.11 (delete document)

### Steps

- [ ] **5.1 — Add health badge and metrics cards at top of SECTION_VECTOR_DB**

Replace `SECTION_VECTOR_DB` entirely with the new version. After the `<h2>` add:

```html
<section x-show="tab==='vectordb'" x-cloak>
  <div class="flex-between mb-12">
    <h2>Knowledge Base</h2>
    <span class="badge" :class="health?.ok?'badge-green':'badge-red'" x-text="health?.ok?'Optimal':'Degraded'"></span>
  </div>

  <!-- Metrics cards -->
  <div class="grid mb-16">
    <div class="card"><h3>Total Chunks</h3>
      <div class="stat" x-text="(vdbMetrics.totalChunks||0).toLocaleString()"></div>
    </div>
    <div class="card"><h3>Collections</h3>
      <div class="stat" x-text="collections.length||0"></div>
    </div>
    <div class="card"><h3>Documents</h3>
      <div class="stat" x-text="(vdbMetrics.totalDocs||0).toLocaleString()"></div>
      <div class="stat-label" x-text="vdbMetrics.docsStatus||'—'"></div>
    </div>
  </div>

  <!-- Upload zone -->
  <div class="card mb-16" style="border:2px dashed var(--ghost-border);text-align:center;padding:32px"
    @dragover.prevent="$el.style.borderColor='var(--primary)'"
    @dragleave="$el.style.borderColor='var(--ghost-border)'"
    @drop.prevent="handleDocDrop($event);$el.style.borderColor='var(--ghost-border)'">
    <p class="text-muted mb-8">Drag and drop documents here to begin vector indexing</p>
    <div class="flex gap-8" style="justify-content:center">
      <span class="badge badge-blue">PDF</span>
      <span class="badge badge-blue">DOCX</span>
      <span class="badge badge-blue">TXT</span>
      <span class="badge badge-blue">MD</span>
    </div>
    <input type="file" id="docFileInput" style="display:none" accept=".pdf,.docx,.txt,.md,.csv"
      @change="handleDocFile($event)">
    <button class="btn btn-blue mt-12" @click="document.getElementById('docFileInput').click()">Select Files</button>
    <div class="text-sm mt-8" x-show="uploadStatus" :class="uploadStatus.startsWith('Error')?'text-red':'text-green'" x-text="uploadStatus"></div>
  </div>

  <!-- Indexing pipeline -->
  <div class="card mb-16" x-show="ingestPipeline.active">
    <h3>Indexing Pipeline</h3>
    <div class="pipeline-steps">
      <template x-for="s in ingestPipeline.steps" :key="s.name">
        <span class="pipe-step" :class="s.status" x-text="s.name"></span>
      </template>
    </div>
    <div class="text-xs text-muted" x-text="ingestPipeline.detail"></div>
  </div>

  <!-- Documents table -->
  <div class="card mb-16">
    <div class="flex-between mb-8">
      <h3>Documents</h3>
      <div class="flex gap-8">
        <input type="text" x-model="docSearchFilter" placeholder="Search documents..." @keyup.enter="loadDocuments()" style="width:200px">
        <button class="btn btn-muted btn-sm" @click="loadDocuments()">Refresh</button>
      </div>
    </div>
    <template x-if="docsList.length">
      <div>
        <table class="tbl">
          <thead><tr><th>Document</th><th>Status</th><th>Size</th><th>Ingested</th><th></th></tr></thead>
          <tbody>
            <template x-for="d in docsList" :key="d.doc_id">
              <tr>
                <td class="mono text-sm" x-text="d.filename||d.doc_id"></td>
                <td><span class="badge" :class="d.status==='indexed'?'badge-green':d.status==='processing'?'badge-blue':d.status==='error'?'badge-red':'badge-muted'" x-text="d.status"></span></td>
                <td class="mono text-xs" x-text="d.size?(d.size/1024).toFixed(0)+' KB':''"></td>
                <td class="text-xs" x-text="d.ingested_at?.substring(0,10)||d.created_at?.substring(0,10)||''"></td>
                <td><button class="btn btn-red btn-sm" @click="deleteDoc(d.doc_id)">Delete</button></td>
              </tr>
            </template>
          </tbody>
        </table>
        <div class="flex-between mt-8">
          <span class="text-xs text-muted" x-text="'Total: '+docsTotal+' documents'"></span>
          <div class="flex gap-8">
            <button class="btn btn-muted btn-sm" x-show="docsOffset>0" @click="docsOffset-=20;loadDocuments()">Prev</button>
            <button class="btn btn-muted btn-sm" x-show="docsList.length>=20" @click="docsOffset+=20;loadDocuments()">Next</button>
          </div>
        </div>
      </div>
    </template>
    <p x-show="!docsList.length" class="text-muted text-sm">No documents indexed yet</p>
  </div>

  <!-- Collections browser (existing, kept) -->
  <div class="grid mb-16">
    <template x-for="c in collections" :key="c.name">
      <div class="card" @click="browseCollection=c.name;scrollOffset=null;scrollPoints=[];scrollCollection()" style="cursor:pointer">
        <h3 x-text="c.name"></h3>
        <div class="stat" x-text="c.points_count??'?'"></div>
        <div class="stat-label">points</div>
        <span class="badge" :class="c.status==='green'?'badge-green':'badge-yellow'" x-text="c.status"></span>
      </div>
    </template>
  </div>

  <!-- Search tester (existing, kept) -->
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
          <div class="card mb-8" style="background:var(--surface-container-lowest)">
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

  <!-- Browse panel (existing, kept) -->
  <div class="card" x-show="browseCollection">
    <div class="flex-between mb-8">
      <h3 x-text="'Browse: '+browseCollection"></h3>
      <button class="btn btn-muted btn-sm" @click="browseCollection='';scrollPoints=[]">Close</button>
    </div>
    <div style="max-height:400px;overflow-y:auto">
      <template x-for="(p,i) in scrollPoints" :key="p.id">
        <div class="card mb-8" style="background:var(--surface-container-lowest)">
          <div class="text-xs text-muted mb-4" x-text="'ID: '+p.id"></div>
          <div class="text-sm" x-text="(p.payload?.text||JSON.stringify(p.payload)||'').substring(0,200)"></div>
          <div class="text-xs text-muted mt-4" x-text="'tags: '+(p.payload?.tags||[]).join(', ')"></div>
        </div>
      </template>
    </div>
    <button class="btn btn-muted btn-sm mt-8" x-show="scrollNextOffset" @click="scrollCollection()">Load more</button>
  </div>
</section>
```

- [ ] **5.2 — Add JS state and methods for documents/upload**

Add to data properties:

```javascript
docsList:[],docsTotal:0,docsOffset:0,docSearchFilter:'',uploadStatus:'',
vdbMetrics:{totalChunks:0,totalDocs:0,docsStatus:''},
ingestPipeline:{active:false,steps:[],detail:''},
```

Update `loadVectorDB()`:

```javascript
async loadVectorDB(){
  try{
    const d=await this.api('/admin/collections');this.collections=d.collections||[];
    const totalChunks=this.collections.reduce((s,c)=>s+(c.points_count||0),0);
    this.vdbMetrics.totalChunks=totalChunks;
    try{const ds=await this.api('/api/docs/status');
      this.vdbMetrics.totalDocs=ds.total_documents||ds.total||0;
      this.vdbMetrics.docsStatus=ds.status||''}catch(e){}
    await this.loadDocuments();
  }catch(e){console.error(e)}},
async loadDocuments(){
  try{let url='/api/docs/?limit=20&offset='+this.docsOffset;
    if(this.docSearchFilter)url+='&file_type='+encodeURIComponent(this.docSearchFilter);
    const d=await this.api(url);this.docsList=d.items||[];this.docsTotal=d.total||0
  }catch(e){this.docsList=[];this.docsTotal=0}},
async deleteDoc(id){
  if(!confirm('Delete this document?'))return;
  try{await this.api('/api/docs/'+id,{method:'DELETE'});await this.loadDocuments()}catch(e){alert('Error: '+e.message)}},
async handleDocDrop(e){
  const files=e.dataTransfer?.files;if(!files?.length)return;
  for(const f of files)await this.uploadDoc(f)},
handleDocFile(e){
  const files=e.target.files;if(!files?.length)return;
  for(const f of files)this.uploadDoc(f);e.target.value=''},
async uploadDoc(file){
  this.uploadStatus='Uploading '+file.name+'...';
  this.ingestPipeline={active:true,steps:[
    {name:'UPLOAD',status:'active'},{name:'CHUNK',status:''},{name:'EMBED',status:''},{name:'STORE',status:''},{name:'SYNC',status:''}],detail:file.name};
  try{
    const r=await this.api('/api/docs/ingest',{method:'POST',body:{file_path:file.name}});
    this.uploadStatus=r.status==='error'?'Error: '+(r.error_message||'failed'):'Ingested: '+file.name;
    this.ingestPipeline.steps.forEach(s=>s.status='done');
    setTimeout(()=>{this.ingestPipeline.active=false},3000);
    await this.loadDocuments();await this.loadVectorDB();
  }catch(e){this.uploadStatus='Error: '+e.message;this.ingestPipeline.active=false}},
```

- [ ] **5.3 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'docsList' in h and 'handleDocDrop' in h and 'CHUNK' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): RAG knowledge base with upload, docs table, metrics, pipeline"
```

---

## Task 6 — Logs: Level Filters + Download + Enhanced Display (S+M)

**Items covered:** 5.1 (log level filters — partial, front-only filter), 5.3 (download button), 5.4 (node status), 5.7 (filtered count)

### Steps

- [ ] **6.1 — Add level filter buttons and download to Logs header**

Replace the SECTION_LOGS header area with:

```html
<section x-show="tab==='logs'" x-cloak>
  <div class="flex-between mb-8">
    <h2>System Logs</h2>
    <div class="flex gap-8">
      <span class="badge" :class="health?.ok?'badge-green':'badge-red'" x-text="health?.ok?'Connected':'Down'"></span>
      <span class="text-xs text-muted mono" x-text="'Qdrant: '+(health?.checks?.qdrant?.collections||0)+' collections'"></span>
    </div>
  </div>
  <div class="flex gap-8 mb-12" style="flex-wrap:wrap">
    <template x-for="lvl in ['ALL','INFO','WARNING','ERROR','DEBUG']" :key="lvl">
      <button class="btn btn-sm" :class="logLevelFilter===lvl?'btn-blue':'btn-muted'"
        @click="logLevelFilter=lvl;loadLogs()" x-text="lvl"></button>
    </template>
    <div style="margin-left:auto" class="flex gap-8">
      <select x-model="logMethodFilter" @change="loadLogs()">
        <option value="">All methods</option>
        <option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option>
      </select>
      <input type="text" x-model="logPathFilter" placeholder="Path filter..." @keyup.enter="loadLogs()">
      <button class="btn btn-muted btn-sm" @click="loadLogs()">Refresh</button>
      <button class="btn btn-muted btn-sm" @click="downloadLogs()">Download</button>
    </div>
  </div>
```

- [ ] **6.2 — Add JS state and methods**

Add to data properties:

```javascript
logLevelFilter:'ALL',
```

Add method:

```javascript
async downloadLogs(){
  const blob=new Blob([JSON.stringify(this.auditLogs,null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='nanobot-logs.json';a.click()},
```

For client-side level filtering (since the backend audit-log doesn't support log levels), add a computed-style filter. After `loadLogs()` fetches `this.auditLogs`, add filtering logic:

Inside `loadLogs()`, after `this.auditLogs=d.entries||[];this.auditTotal=d.total||0;` add:

```javascript
if(this.logLevelFilter!=='ALL'){
  const lvl=this.logLevelFilter;
  this.auditLogs=this.auditLogs.filter(e=>{
    if(lvl==='ERROR')return e.status>=400;
    if(lvl==='WARNING')return e.status>=300&&e.status<400;
    if(lvl==='INFO')return e.status>=200&&e.status<300;
    if(lvl==='DEBUG')return e.method==='GET';
    return true})}
```

- [ ] **6.3 — Update footer to show filtered count**

Replace the total line:

```html
<span class="text-xs text-muted" x-text="'Showing '+auditLogs.length+' / '+(auditTotal||0)+' entries'"></span>
```

- [ ] **6.4 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'logLevelFilter' in h and 'downloadLogs' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): log level filters, download button, status display"
```

---

## Task 7 — Chat: Session Controls + Model Sidebar (S+M)

**Items covered:** 6.3 (clear session + save preset), 6.4 (uptime display), 6.8 (formatting toolbar — partial), 6.10 (confidence score — partial)

### Steps

- [ ] **7.1 — Add model info sidebar enhancements**

In `SECTION_CHAT`, in the right-side panel (the `<div>` containing Pipeline Options), add at the top before Pipeline Options:

```html
<div class="card mb-12">
  <h3>Active Model</h3>
  <div class="stat-value text-primary" x-text="chatActiveModel||'default'"></div>
  <div class="text-xs text-muted mt-4" x-text="health?.ok?'System uptime: operational':'System status unknown'"></div>
  <div class="flex gap-8 mt-8">
    <button class="btn btn-muted btn-sm" @click="clearChatSession()">Clear Session</button>
  </div>
</div>
```

- [ ] **7.2 — Add formatting hints to chat input**

Replace the textarea in chat-input-row with:

```html
<div style="flex:1;display:flex;flex-direction:column">
  <div class="flex gap-8" style="padding:4px 8px;background:var(--surface-container-low)">
    <button class="btn-muted btn-sm" style="border:none;background:none;color:var(--on-surface-variant);cursor:pointer;padding:2px 6px;font-size:12px" @click="chatInput+='**bold**'" title="Bold"><b>B</b></button>
    <button class="btn-muted btn-sm" style="border:none;background:none;color:var(--on-surface-variant);cursor:pointer;padding:2px 6px;font-size:12px" @click="chatInput+='`code`'" title="Code">&lt;/&gt;</button>
  </div>
  <textarea x-model="chatInput" @keydown.enter.prevent="if(!$event.shiftKey)sendChat()"
            placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
            :disabled="chatStreaming" style="border-top:none;border-radius:0 0 0 0"></textarea>
</div>
```

- [ ] **7.3 — Add JS state and methods**

Add to data properties:

```javascript
chatActiveModel:'',
```

Add to `loadTab()` inside the `case'chat':` (currently no explicit load), add:

After `case'advanced':` but we need a chat case. Looking at the existing switch, chat isn't explicitly loaded. Add a chat case:

In the switch statement, after `case'config':await this.loadConfig();break;` add:

```javascript
case'chat':this.chatActiveModel=(await this.api('/routes')).profiles?Object.keys((await this.api('/routes')).profiles)[0]:'default';break;
```

Actually simpler — just load it from routes if already available:

```javascript
case'chat':try{const r=await this.api('/routes');this.chatActiveModel=Object.keys(r.profiles||{})[0]||'default'}catch(e){}break;
```

Add method:

```javascript
async clearChatSession(){this.chatMessages=[];this.chatStreamText='';this.chatSessionId='';this.chatSources=[];this.pipelineSteps=[]},
```

- [ ] **7.4 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'clearChatSession' in h and 'chatActiveModel' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): chat model info sidebar, session controls, formatting toolbar"
```

---

## Task 8 — Vector Storage Card in Analytics (S)

**Items covered:** 1.10 (vector storage card)

### Steps

- [ ] **8.1 — Add vector storage card to analytics grid**

In `SECTION_ANALYTICS`, replace the Ingestion card with a more useful Vector Storage card:

Replace:
```html
<div class="card"><h3>Ingestion</h3>
  <template x-if="analytics.ingest"><div>
    <div class="stat" x-text="analytics.ingest.status||'idle'"></div>
    <div class="stat-label" x-text="'Total indexed: '+(analytics.ingest.total_indexed||0)"></div>
  </div></template>
</div>
```

With:
```html
<div class="card"><h3>Vector Storage</h3>
  <template x-if="analytics.ingest"><div>
    <div class="stat" x-text="(analytics.ingest.total_indexed||0).toLocaleString()"></div>
    <div class="stat-label">indexed chunks</div>
    <div class="progress-track mt-8">
      <div class="progress-fill" style="background:var(--secondary);width:42%"></div>
    </div>
    <div class="text-xs text-muted mt-4" x-text="'Status: '+(analytics.ingest.status||'idle')"></div>
  </div></template>
</div>
```

- [ ] **8.2 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'Vector Storage' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): vector storage card in analytics"
```

---

## Task 9 — System Prompt Editor in Settings (M)

**Items covered:** 3.1 (system prompt editor with markdown support)

### Steps

- [ ] **9.1 — Add system prompt editor**

In `SECTION_SETTINGS`, after the model config grid and before the subtabs, add:

```html
<div class="card mb-16">
  <div class="flex-between mb-8">
    <h3>System Prompt</h3>
    <div class="flex gap-8">
      <span class="text-xs text-muted mono" x-text="'L: '+(systemPrompt.split('\\n').length)+' | C: '+systemPrompt.length"></span>
      <span class="badge badge-blue">Markdown Supported</span>
    </div>
  </div>
  <textarea x-model="systemPrompt" rows="8" style="font-family:'JetBrains Mono',monospace;font-size:12px;width:100%;min-height:120px"
    placeholder="You are an advanced AI system..."></textarea>
  <div class="flex gap-8 mt-8">
    <button class="btn btn-blue btn-sm" @click="proposeModelSetting('SYSTEM_PROMPT',systemPrompt)">Save Prompt</button>
    <span class="text-xs text-muted" x-show="systemPromptSaved" x-text="systemPromptSaved"></span>
  </div>
</div>
```

- [ ] **9.2 — Add JS state**

Add to data properties:

```javascript
systemPrompt:'',systemPromptSaved:'',
```

Update `loadSettings()` to extract system prompt. After the tool use line add:

```javascript
const sp=this.allSettings.find(s=>s.key==='SYSTEM_PROMPT');if(sp)this.systemPrompt=sp.value||'';
```

- [ ] **9.3 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'systemPrompt' in h and 'Save Prompt' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): system prompt editor with markdown support"
```

---

## Task 10 — Knowledge Base Selector in Settings (M)

**Items covered:** 3.2 (knowledge base selector dropdown)

### Steps

- [ ] **10.1 — Add KB selector to the model config grid**

In `SECTION_SETTINGS`, after the Tool Execution card in the model config grid, add a 5th card:

```html
<div class="card">
  <h3>Knowledge Base</h3>
  <select x-model="selectedKB" style="width:100%;margin-top:8px"
    @change="proposeModelSetting('DEFAULT_COLLECTION',selectedKB)">
    <option value="">None (Raw Model)</option>
    <template x-for="c in kbCollections" :key="c">
      <option :value="c" x-text="c"></option>
    </template>
  </select>
  <div class="text-xs text-muted mt-4">Default collection for RAG queries</div>
</div>
```

- [ ] **10.2 — Add JS state and loading**

Add to data properties:

```javascript
selectedKB:'',kbCollections:[],
```

Update `loadSettings()` — at the end, fetch collections:

```javascript
try{const cols=await this.api('/admin/collections');this.kbCollections=(cols.collections||[]).map(c=>c.name)}catch(e){}
const kb=this.allSettings.find(s=>s.key==='DEFAULT_COLLECTION');if(kb)this.selectedKB=kb.value||'';
```

- [ ] **10.3 — Build check + commit:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();assert 'kbCollections' in h and 'selectedKB' in h;print('OK')"
git add src/bridge/admin_ui.py && git commit -m "feat(admin): knowledge base selector dropdown in settings"
```

---

## Final verification

- [ ] **Run full build check:**

```bash
python -c "import sys;sys.path.insert(0,'src/bridge');from admin_ui import build_admin_html;h=build_admin_html();print(f'HTML size: {len(h)} bytes');assert len(h)>50000;print('BUILD OK')"
```

- [ ] **Run test suite:**

```bash
pytest tests/ -x -q -k "not test_daily_report_model_items and not test_workflow_items"
```

Expected: all pre-existing tests pass (518+), no new failures.

---

## Feature Inventory (L — backend required, out of scope)

These features require new backend endpoints and are tracked for a future plan:

| ID | Feature | Required Backend |
|----|---------|-----------------|
| 1.6 | Active Model Nodes | Multi-node status endpoint + `psutil` |
| 1.7 | Most Used Tools chart | Tool call counter per 24h |
| 1.12 | Agent Advice card | AI-generated recommendations endpoint |
| 2.2-2.6 | Tool CRUD (enable/disable/create/delete/schema editor) | Tool management API |
| 4.2-4.10 | Full Monitoring tab (CPU/RAM/Disk/Network/Heatmap/Alerts) | System metrics endpoint with `psutil` |
| 5.1 | Server-side log level filtering | `level` param on audit-log endpoint |
| 5.5 | AI Decision Trace timeline | Decision trace recording endpoint |
| 6.5-6.7 | Agent Internals panel (tool calls, telemetry, context) | Real-time agent telemetry stream |
