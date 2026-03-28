"""Admin web UI — single-page application for nanobot-stack management.

Served at GET /admin.  Uses Alpine.js for reactivity and Chart.js for charts.
The entire HTML/CSS/JS is stored as Python string constants (same pattern as
dashboard.py) — no build step, no static files, no Jinja2.
"""
from __future__ import annotations
import os

ADMIN_ENABLED = os.getenv("ADMIN_UI_ENABLED", "true").lower() == "true"
PWA_ENABLED = os.getenv("PWA_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
ADMIN_CSS = """
/* === Neon Observatory Design System === */
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600&family=Space+Grotesk:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

:root{
  /* Surface hierarchy (tonal depth) */
  --surface:#060e20;--surface-dim:#060e20;--surface-container-lowest:#000000;
  --surface-container-low:#091328;--surface-container:#0f1930;
  --surface-container-high:#141f38;--surface-container-highest:#192540;
  --surface-bright:#1f2b49;--surface-variant:#192540;
  /* Primary (electric violet) */
  --primary:#bd9dff;--primary-dim:#8a4cfc;--primary-container:#b28cff;
  --on-primary:#3c0089;--on-primary-container:#2e006c;
  /* Secondary (cyan) */
  --secondary:#53ddfc;--secondary-dim:#40ceed;--secondary-container:#00687a;
  /* Tertiary (emerald) */
  --tertiary:#9bffce;--tertiary-dim:#58e7ab;--tertiary-container:#69f6b8;
  --on-tertiary-container:#005a3c;
  /* Error */
  --error:#ff6e84;--error-dim:#d73357;--error-container:#a70138;
  /* Text */
  --on-surface:#dee5ff;--on-surface-variant:#a3aac4;
  /* Outline */
  --outline:#6d758c;--outline-variant:#40485d;
  /* Semantic aliases (backward compat) */
  --bg:var(--surface);--card:var(--surface-container);
  --border:rgba(64,72,93,.15);--ghost-border:rgba(64,72,93,.15);
  --text:var(--on-surface);--muted:var(--on-surface-variant);
  --green:var(--tertiary-dim);--red:var(--error);--yellow:#eab308;
  --blue:var(--primary);--purple:var(--primary-dim);--cyan:var(--secondary);
  --input-bg:var(--surface-container-lowest);--hover:var(--surface-container-high);
  --active-tab:var(--primary);
  /* Gradient */
  --gradient-cta:linear-gradient(135deg,var(--primary-dim),var(--primary));
}

*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:var(--surface);color:var(--on-surface);
font-size:0.875rem;line-height:1.5;-webkit-font-smoothing:antialiased}
[x-cloak]{display:none!important}
a{color:var(--primary);text-decoration:none}
a:hover{text-decoration:underline}
h1,h2{font-family:'Manrope',sans-serif;font-weight:700;color:var(--on-surface);letter-spacing:-.02em}
h2{font-size:1.5rem}
h3{font-family:'Space Grotesk',sans-serif;font-weight:500}

/* === Nav (no-border: tonal shift only) === */
.topnav{background:var(--surface-container-low);padding:0 20px;
display:flex;align-items:center;position:sticky;top:0;z-index:100;overflow-x:auto;
backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
.topnav .brand{font-family:'Manrope',sans-serif;font-weight:800;font-size:15px;
color:var(--secondary);padding:14px 20px 14px 0;white-space:nowrap;
margin-right:12px;border-right:1px solid var(--ghost-border)}
.topnav a.tab{padding:14px 16px;color:var(--on-surface-variant);white-space:nowrap;
font-family:'Space Grotesk',sans-serif;font-size:13px;font-weight:500;
border-bottom:2px solid transparent;transition:all .2s ease}
.topnav a.tab:hover{color:var(--on-surface);text-decoration:none;background:var(--surface-container)}
.topnav a.tab.active{color:var(--primary);border-bottom-color:var(--primary)}

/* === Layout === */
main{max-width:1400px;margin:0 auto;padding:20px 24px}
section{min-height:200px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:20px}
.grid-wide{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:16px;margin-bottom:20px}

/* === Cards (no border — tonal surface shift) === */
.card{background:var(--surface-container);border:1px solid var(--ghost-border);
border-radius:8px;padding:20px;transition:background .15s ease}
.card:hover{background:var(--surface-container-high)}
.card h3{font-size:0.75rem;color:var(--on-surface-variant);margin-bottom:10px;
text-transform:uppercase;letter-spacing:.08em}

/* === Tables (no divider lines — spacing-based) === */
.tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:0.8125rem}
.tbl th{text-align:left;padding:10px 12px;color:var(--on-surface-variant);
font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:0.6875rem;
text-transform:uppercase;letter-spacing:.08em;
border-bottom:1px solid var(--ghost-border)}
.tbl td{padding:10px 12px;vertical-align:top;border-bottom:1px solid rgba(64,72,93,.08)}
.tbl tr:hover td{background:var(--surface-container-low)}

/* === Forms === */
.form-row{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
input[type=text],input[type=number],input[type=password],textarea,select{
background:var(--surface-container-lowest);border:1px solid var(--ghost-border);
border-radius:4px;color:var(--on-surface);padding:8px 12px;font-size:13px;
font-family:'Inter',sans-serif;transition:border-color .2s,box-shadow .2s}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--primary);
box-shadow:0 0 0 3px rgba(138,76,252,.15)}
textarea{resize:vertical;min-height:60px;width:100%}

/* === Buttons (gradient CTA for primary) === */
.btn{display:inline-flex;align-items:center;gap:4px;padding:6px 14px;border:none;
border-radius:4px;font-family:'Space Grotesk',sans-serif;font-size:12px;font-weight:600;
cursor:pointer;color:#fff;transition:all .2s ease}
.btn:hover{opacity:.9;text-decoration:none;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn-blue{background:var(--gradient-cta)}
.btn-green{background:var(--tertiary-dim);color:var(--on-tertiary-container)}
.btn-red{background:var(--error-dim)}
.btn-yellow{background:var(--yellow);color:#000}
.btn-muted{background:transparent;border:1px solid var(--ghost-border);color:var(--primary)}
.btn-muted:hover{background:var(--surface-container-high)}
.btn-sm{padding:4px 10px;font-size:11px}
.btn-default{background:var(--surface-container-high);color:var(--on-surface-variant)}

/* === Badges === */
.badge{display:inline-block;padding:2px 8px;border-radius:9999px;
font-family:'Space Grotesk',sans-serif;font-size:11px;font-weight:600}
.badge-green{background:rgba(88,231,171,.12);color:var(--tertiary-dim)}
.badge-red{background:rgba(255,110,132,.12);color:var(--error)}
.badge-yellow{background:rgba(234,179,8,.12);color:var(--yellow)}
.badge-blue{background:rgba(189,157,255,.12);color:var(--primary)}
.badge-muted{background:rgba(163,170,196,.1);color:var(--on-surface-variant)}

/* === Stats === */
.stat{font-family:'Manrope',sans-serif;font-size:2rem;font-weight:800;color:var(--on-surface)}
.stat-value{font-family:'JetBrains Mono',monospace;font-size:1.5rem;font-weight:500;color:var(--on-surface)}
.stat-label{font-family:'Space Grotesk',sans-serif;font-size:0.6875rem;
color:var(--on-surface-variant);margin-top:4px;text-transform:uppercase;letter-spacing:.06em}
.mono{font-family:'JetBrains Mono',monospace;font-size:12px}

/* === Diff === */
.diff-line{font-family:'JetBrains Mono',monospace;font-size:12px;padding:1px 8px;
white-space:pre-wrap;word-break:break-all}
.diff-add{background:rgba(88,231,171,.1);color:var(--tertiary-dim)}
.diff-del{background:rgba(255,110,132,.1);color:var(--error)}
.diff-hdr{color:var(--secondary);font-weight:600}

/* === Chat === */
.chat-wrap{display:flex;flex-direction:column;height:60vh;background:var(--surface-container-low);
border-radius:8px;overflow:hidden}
.chat-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.chat-bubble{max-width:80%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.5;word-wrap:break-word}
.chat-user{align-self:flex-end;background:var(--gradient-cta);color:#fff;border-bottom-right-radius:4px}
.chat-bot{align-self:flex-start;background:var(--surface-container);border-bottom-left-radius:4px}
.chat-input-row{display:flex;background:var(--surface-container)}
.chat-input-row textarea{flex:1;border:none;border-radius:0;padding:12px;resize:none;height:48px}
.chat-input-row .btn{border-radius:0;height:48px;padding:0 20px}

/* === Pipeline === */
.pipeline-steps{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.pipe-step{padding:3px 8px;border-radius:4px;font-family:'Space Grotesk',sans-serif;
font-size:11px;background:var(--surface-container-high);color:var(--on-surface-variant)}
.pipe-step.done{background:rgba(88,231,171,.12);color:var(--tertiary-dim)}
.pipe-step.active{background:rgba(189,157,255,.12);color:var(--primary);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}

/* === Modal (glassmorphism) === */
.modal-bg{position:fixed;inset:0;background:rgba(6,14,32,.7);backdrop-filter:blur(8px);
-webkit-backdrop-filter:blur(8px);z-index:200;display:flex;align-items:center;justify-content:center}
.modal{background:var(--surface-variant);border:1px solid var(--ghost-border);
border-radius:12px;padding:24px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;
backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
box-shadow:0 0 32px rgba(138,76,252,.08)}
.modal h3{margin-bottom:14px;font-family:'Manrope',sans-serif}

/* === Chart === */
.chart-container{position:relative;height:220px}

/* === Subsection tabs === */
.subtabs{display:flex;gap:4px;margin-bottom:12px}
.subtabs .btn{border-radius:9999px}
.subtabs .btn.active{background:var(--gradient-cta)}

/* === Telemetry header (real-time pulse bar) === */
.telemetry-bar{background:var(--surface-container-highest);padding:6px 16px;
display:flex;gap:20px;align-items:center;font-family:'Space Grotesk',sans-serif;
font-size:0.6875rem;color:var(--on-surface-variant);border-radius:6px;margin-bottom:16px}
.telemetry-bar .pulse-dot{width:6px;height:6px;border-radius:50%;background:var(--tertiary-dim);
animation:pulse 2s infinite}

/* === Progress bar === */
.progress-track{background:var(--surface-container-high);border-radius:4px;height:8px;overflow:hidden}
.progress-fill{height:100%;border-radius:4px;transition:width .4s ease}

/* === Utility === */
.mb-8{margin-bottom:8px}.mb-12{margin-bottom:12px}.mb-16{margin-bottom:16px}.mb-20{margin-bottom:20px}
.mt-8{margin-top:8px}.mt-12{margin-top:12px}.mt-16{margin-top:16px}
.text-muted{color:var(--on-surface-variant)}.text-green{color:var(--tertiary-dim)}.text-red{color:var(--error)}
.text-primary{color:var(--primary)}.text-secondary{color:var(--secondary)}
.text-sm{font-size:12px}.text-xs{font-size:11px}.text-center{text-align:center}
.flex{display:flex}.flex-between{display:flex;justify-content:space-between;align-items:center}
.gap-8{gap:8px}.gap-12{gap:12px}.gap-16{gap:16px}
.truncate{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;display:inline-block}
.pre-wrap{white-space:pre-wrap;font-family:'JetBrains Mono',monospace;font-size:12px;
background:var(--surface-container-lowest);padding:12px;border-radius:6px;max-height:300px;overflow-y:auto}
.warn-banner{background:rgba(234,179,8,.08);border:1px solid rgba(234,179,8,.2);border-radius:6px;
padding:12px 16px;margin-bottom:14px;color:var(--yellow);font-size:13px}

/* === Scrollbar === */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--surface)}
::-webkit-scrollbar-thumb{background:var(--outline-variant);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--outline)}
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
      <button class="btn btn-blue btn-sm" @click="exportReport()">Export Report</button>
      <button class="btn btn-green btn-sm" @click="showAgentTaskModal=true">New Agent Task</button>
    </div>
  </div>
  <div class="grid">
    <div class="card"><h3>System Pulse</h3>
      <template x-if="health"><div>
        <div class="stat" :class="health.ok?'text-green':'text-red'" x-text="health.ok?'Operational':'Degraded'"></div>
        <div class="stat-label" x-text="'Qdrant: '+(health.checks?.qdrant?.ok?'connected':'down')"></div>
        <div class="stat-label" x-text="'Collections: '+(health.checks?.qdrant?.collections||0)"></div>
        <div class="stat-label" x-text="'API Keys: '+(health.checks?.api_keys?.configured?'configured':'missing')"></div>
      </div></template>
    </div>
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
    <div class="card"><h3>Vector Storage</h3>
      <template x-if="analytics.ingest"><div>
        <div class="stat" x-text="(analytics.ingest.total_indexed||0).toLocaleString()"></div>
        <div class="stat-label">indexed chunks</div>
        <div class="progress-track mt-8">
          <div class="progress-fill" style="background:var(--secondary)" :style="'width:'+Math.min(100,(analytics.ingest.total_indexed||0)/1000*100)+'%'"></div>
        </div>
        <div class="text-xs text-muted mt-4" x-text="'Status: '+(analytics.ingest.status||'idle')"></div>
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
  <div class="grid-wide mb-16">
    <div class="card">
      <h3>Most Used Tools (calls)</h3>
      <div class="chart-container" style="height:260px"><canvas id="mostUsedChart"></canvas></div>
    </div>
    <div class="card" x-show="agentAdvice">
      <h3>Agent Advice</h3>
      <div class="text-sm" x-text="agentAdvice"></div>
      <button class="btn btn-blue btn-sm mt-8" @click="agentAdvice=''">Dismiss</button>
    </div>
  </div>
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
  <!-- Agent Task Modal -->
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
</section>
"""

# ---------------------------------------------------------------------------
# Section: Settings
# ---------------------------------------------------------------------------
SECTION_SETTINGS = """
<section x-show="tab==='settings'" x-cloak>
  <h2 class="mb-12">Model Configuration</h2>
  <div class="warn-banner" x-show="!settingsConfigWriterEnabled">
    CONFIG_WRITER_ENABLED is false. Setting changes via this UI require the config writer to be enabled.
  </div>

  <!-- Model config controls -->
  <div class="grid mb-16">
    <div class="card">
      <h3>Temperature</h3>
      <div class="flex-between">
        <span class="text-xs text-muted">Precise</span>
        <span class="stat-value" x-text="modelTemp.toFixed(2)"></span>
        <span class="text-xs text-muted">Creative</span>
      </div>
      <input type="range" min="0" max="1" step="0.05" x-model.number="modelTemp"
        @change="proposeModelSetting('TEMPERATURE',modelTemp)" style="width:100%;accent-color:var(--primary)">
    </div>
    <div class="card">
      <h3>Top-P</h3>
      <div class="flex-between">
        <span class="text-xs text-muted">Narrow</span>
        <span class="stat-value" x-text="modelTopP.toFixed(2)"></span>
        <span class="text-xs text-muted">Diverse</span>
      </div>
      <input type="range" min="0" max="1" step="0.05" x-model.number="modelTopP"
        @change="proposeModelSetting('TOP_P',modelTopP)" style="width:100%;accent-color:var(--primary)">
    </div>
    <div class="card">
      <h3>Max Tokens</h3>
      <div class="stat-value" x-text="modelMaxTokens.toLocaleString()"></div>
      <input type="range" min="256" max="128000" step="256" x-model.number="modelMaxTokens"
        @change="proposeModelSetting('MAX_TOKENS',modelMaxTokens)" style="width:100%;accent-color:var(--primary)">
      <div class="text-xs text-muted mt-4" x-text="'~'+(modelMaxTokens/128000*100).toFixed(0)+'% context window'"></div>
    </div>
    <div class="card">
      <h3>Tool Execution</h3>
      <div class="flex-between">
        <span class="text-sm" x-text="modelToolUse?'Enabled — AI can call external tools':'Disabled'"></span>
        <button class="btn btn-sm" :class="modelToolUse?'btn-green':'btn-muted'"
          @click="modelToolUse=!modelToolUse;proposeModelSetting('TOOL_USE_ENABLED',modelToolUse)" x-text="modelToolUse?'On':'Off'"></button>
      </div>
    </div>
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
  </div>

  <!-- System prompt editor -->
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
    </div>
  </div>

  <div class="flex gap-8 mb-16">
    <button class="btn btn-muted btn-sm" @click="resetModelDefaults()">Reset Defaults</button>
    <button class="btn btn-blue btn-sm" @click="testConfig()">Test Configuration</button>
    <span class="text-xs text-green" x-show="testConfigResult" x-text="testConfigResult"></span>
  </div>

  <!-- Settings table -->
  <h3 class="mb-8" style="font-size:1rem;color:var(--on-surface)">All Settings</h3>
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
  <h2 class="mb-12">Skills & Tools</h2>
  <p class="text-xs text-muted mb-16">Extend your AI's capabilities by managing external API integrations, internal connectors, and custom function schemas.</p>

  <!-- Plugin cards grid -->
  <div class="grid mb-16">
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
    <div class="card" x-show="!plugins.plugins||!plugins.plugins.length">
      <p class="text-muted">No plugins loaded</p>
    </div>
  </div>

  <!-- Performance metrics -->
  <div class="grid mb-16">
    <div class="card"><h3>Token Cost (24h)</h3>
      <div class="stat-value" x-text="toolStats.totalCost?'$'+toolStats.totalCost.toFixed(2):'$0.00'"></div>
    </div>
    <div class="card"><h3>Total Calls</h3>
      <div class="stat-value" x-text="(toolStats.totalCalls||0).toLocaleString()"></div>
    </div>
    <div class="card"><h3>Models Active</h3>
      <div class="stat-value" x-text="toolStats.modelsActive||0"></div>
    </div>
  </div>

  <div class="grid-wide">
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
  <div class="flex-between mb-12">
    <h2>Knowledge Base</h2>
    <span class="badge" :class="health?.ok?'badge-green':'badge-red'" x-text="health?.ok?'Optimal':'Degraded'"></span>
  </div>
  <div class="grid mb-16">
    <div class="card"><h3>Total Chunks</h3>
      <div class="stat" x-text="(vdbMetrics.totalChunks||0).toLocaleString()"></div>
    </div>
    <div class="card"><h3>Collections</h3>
      <div class="stat" x-text="collections.length||0"></div>
    </div>
    <div class="card"><h3>Documents</h3>
      <div class="stat" x-text="(vdbMetrics.totalDocs||0).toLocaleString()"></div>
      <div class="stat-label" x-text="vdbMetrics.docsStatus||''"></div>
    </div>
  </div>
  <div class="card mb-16" style="border:2px dashed var(--ghost-border);text-align:center;padding:32px"
    @dragover.prevent="$el.style.borderColor='var(--primary)'"
    @dragleave="$el.style.borderColor='var(--ghost-border)'"
    @drop.prevent="handleDocDrop($event);$el.style.borderColor='var(--ghost-border)'">
    <p class="text-muted mb-8">Drag and drop documents here to begin vector indexing</p>
    <div class="flex gap-8" style="justify-content:center">
      <span class="badge badge-blue">PDF</span><span class="badge badge-blue">DOCX</span>
      <span class="badge badge-blue">TXT</span><span class="badge badge-blue">MD</span>
    </div>
    <input type="file" id="docFileInput" style="display:none" accept=".pdf,.docx,.txt,.md,.csv" @change="handleDocFile($event)">
    <button class="btn btn-blue mt-12" @click="document.getElementById('docFileInput').click()">Select Files</button>
    <div class="text-sm mt-8" x-show="uploadStatus" :class="uploadStatus.startsWith('Error')?'text-red':'text-green'" x-text="uploadStatus"></div>
  </div>
  <div class="card mb-16" x-show="ingestPipeline.active">
    <h3>Indexing Pipeline</h3>
    <div class="pipeline-steps">
      <template x-for="s in ingestPipeline.steps" :key="s.name">
        <span class="pipe-step" :class="s.status" x-text="s.name"></span>
      </template>
    </div>
    <div class="text-xs text-muted" x-text="ingestPipeline.detail"></div>
  </div>
  <div class="card mb-16">
    <div class="flex-between mb-8">
      <h3>Documents</h3>
      <div class="flex gap-8">
        <input type="text" x-model="docSearchFilter" placeholder="Filter by type..." @keyup.enter="loadDocuments()" style="width:180px">
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
                <td><span class="badge" :class="d.status==='indexed'?'badge-green':d.status==='processing'?'badge-blue':d.status==='error'?'badge-red':'badge-muted'" x-text="d.status||'unknown'"></span></td>
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
        </div>
      </template>
    </div>
    <button class="btn btn-muted btn-sm mt-8" x-show="scrollNextOffset" @click="scrollCollection()">Load more</button>
  </div>
</section>
"""

# ---------------------------------------------------------------------------
# Section: Logs
# ---------------------------------------------------------------------------
SECTION_LOGS = """
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
      <span class="text-xs text-muted" x-text="'Showing '+auditLogs.length+' / '+(auditTotal||0)+' entries'"></span>
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
          <div style="flex:1;display:flex;flex-direction:column">
            <div class="flex gap-8" style="padding:4px 8px;background:var(--surface-container-low)">
              <button style="border:none;background:none;color:var(--on-surface-variant);cursor:pointer;padding:2px 6px;font-size:12px" @click="chatInput+='**bold**'" title="Bold"><b>B</b></button>
              <button style="border:none;background:none;color:var(--on-surface-variant);cursor:pointer;padding:2px 6px;font-size:12px" @click="chatInput+='`code`'" title="Code">&lt;/&gt;</button>
            </div>
            <textarea x-model="chatInput" @keydown.enter.prevent="if(!$event.shiftKey)sendChat()"
                      placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
                      :disabled="chatStreaming" style="border-top:none"></textarea>
          </div>
          <button class="btn btn-blue" @click="sendChat()" :disabled="chatStreaming">Send</button>
        </div>
      </div>
    </div>
    <div>
      <div class="card mb-12">
        <h3>Active Model</h3>
        <div class="stat-value text-primary" x-text="chatActiveModel||'default'"></div>
        <div class="text-xs text-muted mt-4" x-text="health?.ok?'System uptime: operational':'System status unknown'"></div>
        <div class="flex gap-8 mt-8">
          <button class="btn btn-muted btn-sm" @click="clearChatSession()">Clear Session</button>
        </div>
      </div>
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
      <div class="card mt-12" x-show="chatInternals.length">
        <h3>Agent Internals</h3>
        <div style="max-height:300px;overflow-y:auto">
          <template x-for="(ev,i) in chatInternals" :key="i">
            <div style="padding:4px 0;border-bottom:1px solid var(--ghost-border)">
              <div class="flex gap-8">
                <span class="badge" :class="ev.status==='done'?'badge-green':ev.status==='error'?'badge-red':'badge-blue'" x-text="ev.phase"></span>
                <span class="mono text-xs text-muted" x-text="ev.detail||''"></span>
              </div>
            </div>
          </template>
        </div>
      </div>
      <div class="card mt-12" x-show="chatTraceSteps.length">
        <h3>AI Decision Trace</h3>
        <div style="position:relative;padding-left:16px;border-left:2px solid rgba(64,72,93,.15)">
          <template x-for="(step,i) in chatTraceSteps" :key="i">
            <div style="padding:6px 0;position:relative">
              <div style="position:absolute;left:-21px;top:10px;width:10px;height:10px;border-radius:50%;background:var(--primary)"></div>
              <div class="text-sm" x-text="step.phase"></div>
              <div class="text-xs text-muted" x-text="step.summary||''"></div>
              <span class="badge badge-blue text-xs" x-show="step.confidence" x-text="(step.confidence*100).toFixed(0)+'% confidence'"></span>
            </div>
          </template>
        </div>
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
        <div class="flex-between mb-8">
          <h3 x-text="name"></h3>
          <div class="flex gap-8">
            <span class="badge" :class="info.running?'badge-green':'badge-muted'" x-text="info.running?'running':'stopped'"></span>
            <span class="badge badge-blue" x-show="info.configured">configured</span>
          </div>
        </div>
        <div class="text-sm text-red mb-8" x-show="info.error" x-text="info.error"></div>
        <button class="btn btn-muted btn-sm" x-show="info.running"
          @click="sendTestMessage(name)">Send Test</button>
        <span class="text-xs text-green" x-show="channelTestResult[name]" x-text="channelTestResult[name]"></span>
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
  <div class="card mb-16">
    <h3>Action Trust Levels</h3>
    <p class="text-xs text-muted mb-8">Configure how much autonomy the assistant has per action type. Changes take effect immediately.</p>
    <table class="tbl">
      <tr><th>Action Type</th><th>Trust Level</th><th>Successes</th><th>Failures</th><th>Auto-Promote After</th><th>Actions</th></tr>
      <template x-for="p in trustPolicies" :key="p.action_type">
        <tr>
          <td x-text="p.action_type"></td>
          <td>
            <select :value="p.trust_level" @change="updateTrust(p.action_type,$event.target.value)" style="width:180px">
              <option value="auto">Auto</option>
              <option value="notify_then_execute">Notify then execute</option>
              <option value="approval_required">Approval required</option>
              <option value="blocked">Blocked</option>
            </select>
          </td>
          <td><span class="badge badge-green" x-text="p.successful_executions"></span></td>
          <td><span class="badge badge-red" x-text="p.failed_executions"></span></td>
          <td>
            <input type="number" :value="p.auto_promote_after||10" min="1" max="100" style="width:70px"
              @change="updateAutoPromote(p.action_type,parseInt($event.target.value))">
            <span class="text-xs text-muted" x-show="p.last_promoted_at" x-text="'Last: '+p.last_promoted_at?.substring(0,10)"></span>
          </td>
          <td><button class="btn btn-blue btn-sm" @click="promoteTrust(p.action_type)">Promote</button></td>
        </tr>
      </template>
    </table>
  </div>
  <div class="card">
    <div class="flex-between mb-8">
      <h3>Trust Audit Log</h3>
      <button class="btn btn-muted btn-sm" @click="loadTrust()">Refresh</button>
    </div>
    <table class="tbl">
      <tr><th>Time</th><th>Action</th><th>Detail</th><th>Level</th><th>Outcome</th><th></th></tr>
      <template x-for="a in trustAudit" :key="a.id">
        <tr>
          <td class="mono text-xs" x-text="a.created_at?.substring(0,19)"></td>
          <td x-text="a.action_type"></td>
          <td class="text-xs" x-text="a.action_detail?.substring(0,60)"></td>
          <td><span class="badge badge-blue" x-text="a.trust_level"></span></td>
          <td><span :class="'badge badge-'+(a.outcome==='auto_executed'||a.outcome==='success'?'green':a.outcome==='blocked'?'red':'yellow')" x-text="a.outcome"></span></td>
          <td>
            <button class="btn btn-red btn-sm" x-show="a.outcome==='pending_notify'"
              @click="cancelAudit(a.id)">Cancel</button>
          </td>
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
          <div class="progress-track mt-8">
            <div class="progress-fill"
              :style="'width:'+Math.min(100,costData.usage_percent)+'%;background:'+(costData.usage_percent>80?'var(--error)':costData.usage_percent>50?'var(--yellow)':'var(--tertiary-dim)')"></div>
          </div>
          <div class="text-xs text-muted mt-4" style="font-family:'JetBrains Mono',monospace"
            x-text="costData.daily_tokens_used?.toLocaleString()+' / '+costData.daily_tokens_budget?.toLocaleString()+' tokens'"></div>
        </div>
      </template>
    </div>
    <div class="card">
      <h3>Budget Pressure</h3>
      <template x-if="costData">
        <div>
          <div class="stat" :class="costData.budget_pressure>0.8?'text-red':costData.budget_pressure>0.5?'text-yellow':'text-green'"
            x-text="(costData.budget_pressure*100).toFixed(0)+'%'"></div>
          <div class="stat-label" x-text="costData.budget_pressure>0.8?'High \u2014 models may downgrade to Ollama':'Normal'"></div>
        </div>
      </template>
    </div>
  </div>
  <div class="card mb-16">
    <h3>7-Day Cost History</h3>
    <div class="chart-container">
      <canvas id="costHistoryChart"></canvas>
    </div>
  </div>
  <div class="card">
    <h3>Usage by Model (today)</h3>
    <table class="tbl">
      <tr><th>Model</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th><th>Est. Cost</th></tr>
      <template x-for="m in costByModel" :key="m.model">
        <tr>
          <td x-text="m.model"></td>
          <td class="mono" x-text="m.calls"></td>
          <td class="mono" x-text="m.input_tokens?.toLocaleString()"></td>
          <td class="mono" x-text="m.output_tokens?.toLocaleString()"></td>
          <td class="mono" x-text="'$'+(m.cost_cents/100).toFixed(3)"></td>
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
      <tr><th>Trigger</th><th>Steps</th><th>Freq</th><th>Confidence</th><th>Auto-suggest</th><th>Last Seen</th><th></th></tr>
      <template x-for="w in workflows" :key="w.id">
        <tr>
          <td x-text="w.trigger_pattern"></td>
          <td class="mono" x-text="JSON.parse(w.steps_json||'[]').length"></td>
          <td><span class="badge badge-blue" x-text="w.frequency"></span></td>
          <td><span :class="'badge badge-'+(w.confidence>=0.7?'green':w.confidence>=0.4?'yellow':'red')" x-text="(w.confidence*100).toFixed(0)+'%'"></span></td>
          <td>
            <button class="btn btn-sm" :class="w.auto_suggest?'btn-green':'btn-default'"
              @click="toggleWorkflow(w.id,!w.auto_suggest)" x-text="w.auto_suggest?'On':'Off'"></button>
          </td>
          <td class="mono text-xs" x-text="w.last_observed?.substring(0,10)"></td>
          <td>
            <button class="btn btn-red btn-sm" @click="deleteWorkflow(w.id)">Delete</button>
          </td>
        </tr>
      </template>
      <template x-if="!workflows?.length">
        <tr><td colspan="7" class="text-muted text-center">No workflows learned yet</td></tr>
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
        <h3 x-text="a.name" style="text-transform:none;font-size:0.875rem;color:var(--on-surface)"></h3>
        <p class="text-xs text-muted mt-4" x-text="a.description"></p>
        <div class="mt-8 flex gap-8" x-show="a.tools?.length">
          <template x-for="t in (a.tools||[])" :key="t">
            <span class="badge badge-blue" x-text="t"></span>
          </template>
        </div>
      </div>
    </template>
    <template x-if="!agentList?.length">
      <div class="card"><p class="text-muted">No agents registered. Enable with AGENT_ORCHESTRATOR_ENABLED=true.</p></div>
    </template>
  </div>
  <div class="card mt-16">
    <h3>Recent Executions</h3>
    <table class="tbl">
      <tr><th>Time</th><th>Agent</th><th>Task</th><th>Status</th><th>Tokens</th><th>Est. Cost</th></tr>
      <template x-for="e in agentHistory" :key="e.id||e.timestamp">
        <tr>
          <td class="mono text-xs" x-text="e.timestamp?.substring(0,19)"></td>
          <td x-text="e.agent"></td>
          <td class="text-xs" x-text="e.task?.substring(0,80)"></td>
          <td><span :class="'badge badge-'+(e.status==='completed'?'green':'red')" x-text="e.status"></span></td>
          <td class="mono" x-text="e.tokens?.toLocaleString()"></td>
          <td class="mono" x-text="e.cost_cents!=null?'$'+(e.cost_cents/100).toFixed(3):estimateAgentCost(e.tokens)"></td>
        </tr>
      </template>
      <template x-if="!agentHistory?.length">
        <tr><td colspan="6" class="text-muted text-center">No agent executions yet</td></tr>
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
# Section: Monitoring (Stitch L-feature)
# ---------------------------------------------------------------------------
SECTION_MONITORING = """
<section x-show="tab==='monitoring'" x-cloak>
  <div class="flex-between mb-12">
    <h2>Server Monitoring</h2>
    <div class="flex gap-8">
      <span class="badge badge-green" x-show="monitorSnap.cpu_percent!=null">System Operational</span>
      <span class="text-xs text-muted mono" x-text="monitorSnap.timestamp?.substring(11,19)+' UTC'||''"></span>
      <button class="btn btn-muted btn-sm" @click="loadMonitoring()">Refresh</button>
    </div>
  </div>

  <!-- Key metric cards -->
  <div class="grid mb-16">
    <div class="card">
      <h3>CPU Usage</h3>
      <div class="stat" :class="monitorSnap.cpu_percent>80?'text-red':monitorSnap.cpu_percent>50?'text-yellow':'text-green'"
        x-text="(monitorSnap.cpu_percent||0).toFixed(1)+'%'"></div>
      <div class="progress-track mt-8">
        <div class="progress-fill" :style="'width:'+Math.min(100,monitorSnap.cpu_percent||0)+'%;background:var(--'+(monitorSnap.cpu_percent>80?'error':monitorSnap.cpu_percent>50?'yellow':'tertiary-dim')+')'"></div>
      </div>
    </div>
    <div class="card">
      <h3>RAM Allocation</h3>
      <div class="stat-value" x-text="(monitorSnap.ram_used_gb||0).toFixed(1)+' / '+(monitorSnap.ram_total_gb||0).toFixed(1)+' GB'"></div>
      <div class="progress-track mt-8">
        <div class="progress-fill" style="background:var(--secondary)" :style="'width:'+(monitorSnap.ram_percent||0)+'%'"></div>
      </div>
      <div class="text-xs text-muted mt-4" x-text="(monitorSnap.ram_percent||0).toFixed(1)+'%'"></div>
    </div>
    <div class="card">
      <h3>Disk Usage</h3>
      <div class="stat-value" x-text="(monitorSnap.disk_used_gb||0).toFixed(1)+' / '+(monitorSnap.disk_total_gb||0).toFixed(1)+' GB'"></div>
      <div class="progress-track mt-8">
        <div class="progress-fill" style="background:var(--primary)" :style="'width:'+(monitorSnap.disk_percent||0)+'%'"></div>
      </div>
    </div>
    <div class="card">
      <h3>Network I/O</h3>
      <div class="text-sm"><span class="text-muted">Sent:</span> <span class="mono" x-text="(monitorSnap.net_sent_mb||0).toLocaleString()+' MB'"></span></div>
      <div class="text-sm"><span class="text-muted">Recv:</span> <span class="mono" x-text="(monitorSnap.net_recv_mb||0).toLocaleString()+' MB'"></span></div>
      <div class="text-xs text-muted mt-8" x-text="'Uptime: '+formatUptime(monitorSnap.uptime_seconds||0)"></div>
    </div>
  </div>

  <!-- Live resource history chart -->
  <div class="card mb-16">
    <h3>Live Resource History (60 min)</h3>
    <div class="chart-container">
      <canvas id="resourceHistoryChart"></canvas>
    </div>
  </div>

  <!-- System Load Heatmap -->
  <div class="card mb-16">
    <h3>System Load Heatmap (24h)</h3>
    <div style="display:flex;flex-wrap:wrap;gap:2px;margin-top:8px">
      <template x-for="(slot,i) in monitorHeatmap" :key="i">
        <div :title="slot.t?.substring(11,16)+' CPU:'+slot.cpu+'%'"
          :style="'width:12px;height:12px;border-radius:2px;background:rgba('+(slot.cpu>80?'255,110,132':slot.cpu>50?'234,179,8':'88,231,171')+','+(Math.max(0.1,slot.cpu/100))+')'"></div>
      </template>
    </div>
    <div class="flex-between mt-4 text-xs text-muted">
      <span>24h ago</span><span>Now</span>
    </div>
  </div>

  <!-- Critical Alerts -->
  <div class="card">
    <div class="flex-between mb-8">
      <h3>System Alerts</h3>
      <span class="badge badge-muted" x-text="monitorAlerts.length+' alerts'"></span>
    </div>
    <template x-if="monitorAlerts.length">
      <div style="max-height:300px;overflow-y:auto">
        <template x-for="(a,i) in monitorAlerts" :key="i">
          <div style="padding:10px 0;border-bottom:1px solid var(--ghost-border)">
            <div class="flex gap-8">
              <span class="badge" :class="a.level==='high'?'badge-red':'badge-yellow'" x-text="a.level"></span>
              <span class="text-sm" style="font-weight:600" x-text="a.title"></span>
              <span class="text-xs text-muted" style="margin-left:auto" x-text="a.ts?.substring(11,19)"></span>
            </div>
            <div class="text-xs text-muted mt-4" x-text="a.detail"></div>
          </div>
        </template>
      </div>
    </template>
    <p x-show="!monitorAlerts.length" class="text-muted text-sm">No alerts — all systems nominal</p>
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
    {id:'agents',label:'Agents'},{id:'monitoring',label:'Monitoring'},
    {id:'advanced',label:'Advanced'},{id:'scheduler',label:'Scheduler'}
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
  channelStatus:{},pendingPairings:[],approvedUsers:[],dmPolicy:'pairing',channelTestResult:{},

  // --- Shell ---
  pendingActions:[],actionHistory:[],elevatedCommands:{},proposeCmd:'',proposeDesc:'',proposeResult:'',

  // --- Config ---
  pendingConfigs:[],configHistory:[],diffPreview:'',

  // --- Trust (v10) ---
  trustPolicies:[],trustAudit:[],

  // --- Costs (v10) ---
  costData:null,costByModel:[],costHistory:[],costHistoryChart:null,

  // --- Workflows (v10) ---
  workflows:[],

  // --- Agents (v10) ---
  agentList:[],agentHistory:[],

  // --- Advanced ---
  kgQuery:'',kgResult:null,piiText:'',piiResult:null,explainQuery:'',explainResult:null,

  // --- Stitch features ---
  recentActivity:[],showAgentTaskModal:false,agentTaskInput:'',agentTaskResult:null,
  modelTemp:0.7,modelTopP:0.9,modelMaxTokens:4096,modelToolUse:true,testConfigResult:'',
  systemPrompt:'',selectedKB:'',kbCollections:[],
  toolStats:{totalCost:0,totalCalls:0,modelsActive:0},
  docsList:[],docsTotal:0,docsOffset:0,docSearchFilter:'',uploadStatus:'',
  vdbMetrics:{totalChunks:0,totalDocs:0,docsStatus:''},
  ingestPipeline:{active:false,steps:[],detail:''},
  logLevelFilter:'ALL',chatActiveModel:'',
  // --- Monitoring ---
  monitorSnap:{},monitorHeatmap:[],monitorAlerts:[],resourceChart:null,
  // --- Agent Internals ---
  chatInternals:[],chatTraceSteps:[],
  // --- Most Used Tools ---
  mostUsedTools:[],mostUsedChart:null,agentAdvice:'',

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
        case'chat':try{const r=await this.api('/routes');this.chatActiveModel=Object.keys(r.profiles||{})[0]||'default'}catch(e){}break;
        case'trust':await this.loadTrust();break;
        case'costs':await this.loadCosts();break;
        case'workflows':await this.loadWorkflows();break;
        case'agents':await this.loadAgents();break;
        case'monitoring':await this.loadMonitoring();break;
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
      this.health=h;this.analytics={cbs:cb,cache:ca,rates:ra,feedback:fb,ingest:ig,profile:pr,kg:kg,tokenStats:ts,wm:wm,plugins:pl,routes:ro};
      this.loadRecentActivity();this.loadMostUsedTools();this.generateAdvice(ts);
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
      const t=this.allSettings.find(s=>s.key==='TEMPERATURE');if(t)this.modelTemp=parseFloat(t.value)||0.7;
      const tp=this.allSettings.find(s=>s.key==='TOP_P');if(tp)this.modelTopP=parseFloat(tp.value)||0.9;
      const mt=this.allSettings.find(s=>s.key==='MAX_TOKENS');if(mt)this.modelMaxTokens=parseInt(mt.value)||4096;
      const tu=this.allSettings.find(s=>s.key==='TOOL_USE_ENABLED');if(tu)this.modelToolUse=tu.value==='true';
      const sp=this.allSettings.find(s=>s.key==='SYSTEM_PROMPT');if(sp)this.systemPrompt=sp.value||'';
      const kb=this.allSettings.find(s=>s.key==='DEFAULT_COLLECTION');if(kb)this.selectedKB=kb.value||'';
      try{const cols=await this.api('/admin/collections');this.kbCollections=(cols.collections||[]).map(c=>c.name)}catch(e){}
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
      try{const ts=await this.api('/token-stats');const bm=ts.by_model||{};
        this.toolStats={totalCost:Object.values(bm).reduce((s,m)=>s+(m.cost||0),0),
          totalCalls:Object.values(bm).reduce((s,m)=>s+(m.calls||0),0),
          modelsActive:Object.keys(bm).length}}catch(e){}
    }catch(e){console.error('loadTools:',e)}
  },
  async previewRoute(){
    if(!this.routePreviewTask)return;
    try{this.routePreviewResult=await this.api('/route-preview',{method:'POST',body:{task_type:this.routePreviewTask}})}catch(e){console.error(e)}
  },

  // === Vector DB ===
  async loadVectorDB(){
    try{const d=await this.api('/admin/collections');this.collections=d.collections||[];
      this.vdbMetrics.totalChunks=this.collections.reduce((s,c)=>s+(c.points_count||0),0);
      try{const ds=await this.api('/api/docs/status');this.vdbMetrics.totalDocs=ds.total_documents||ds.total||0;this.vdbMetrics.docsStatus=ds.status||''}catch(e){}
      await this.loadDocuments()}catch(e){console.error(e)}
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
      if(this.logLevelFilter&&this.logLevelFilter!=='ALL')url+='&level='+this.logLevelFilter;
      const d=await this.api(url);
      this.auditLogs=d.entries||[];this.auditTotal=d.total||0;
    }catch(e){console.error(e)}
  },

  // === Chat ===
  async sendChat(){
    if(!this.chatInput.trim()||this.chatStreaming)return;
    const text=this.chatInput.trim();this.chatInput='';
    this.chatMessages.push({role:'user',content:text});
    this.chatStreaming=true;this.chatStreamText='';this.chatSources=[];this.chatInternals=[];this.chatTraceSteps=[];
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
      // Agent Internals capture
      const detail=data.result?JSON.stringify(data.result).substring(0,120):(data.hyde_used!=null?'HyDE: '+data.hyde_used:'');
      this.chatInternals.push({phase:data.phase||'',status:data.status||'',detail});
      // AI Decision Trace
      if(data.status==='done'&&data.result){
        const traceStep={phase:data.phase,summary:''};
        if(data.result.task_type)traceStep.summary='Task: '+data.result.task_type;
        if(data.result.tone)traceStep.summary='Tone: '+data.result.tone;
        if(data.result.confidence!=null){traceStep.confidence=data.result.confidence}
        if(data.results_count!=null)traceStep.summary=data.results_count+' results retrieved';
        this.chatTraceSteps.push(traceStep)}
    }else if(evt==='answer'){
      this.chatStreamText=data.text||'';
      if(data.sources)this.chatSources=data.sources;
      this.pipelineSteps.forEach(s=>{if(s.status)s.status='done'});
      this.chatTraceSteps.push({phase:'answer',summary:'Response generated in '+(data.elapsed_seconds||0)+'s',confidence:null});
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
  async sendTestMessage(channel){
    this.channelTestResult[channel]='Sending...';
    try{const r=await this.api('/channels/test',{method:'POST',body:{channel,message:'Test message from nanobot admin UI'}});
      this.channelTestResult[channel]=r.ok?'Sent!':'Failed: '+(r.error||'unknown')}catch(e){this.channelTestResult[channel]='Error: '+e.message}},
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

  // === Stitch features ===
  async loadRecentActivity(){
    try{const d=await this.api('/agent/history?limit=10');this.recentActivity=d.executions||[]}catch(e){console.error(e)}},
  async exportReport(){
    try{const d=await this.api('/token-stats');const blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='nanobot-report.json';a.click()}catch(e){alert('Export failed: '+e.message)}},
  async runAgentTask(){
    if(!this.agentTaskInput.trim())return;this.agentTaskResult=null;
    try{this.agentTaskResult=await this.api('/agent/run',{method:'POST',body:{task:this.agentTaskInput}});
      this.agentTaskInput='';await this.loadRecentActivity()}catch(e){this.agentTaskResult={error:e.message}}},
  async proposeModelSetting(key,val){
    try{await this.api('/settings/key/'+key,{method:'POST',body:{value:String(val),description:'Changed via admin model controls'}});
      this.testConfigResult='Setting '+key+' proposed'}catch(e){alert('Error: '+e.message)}},
  async resetModelDefaults(){this.modelTemp=0.7;this.modelTopP=0.9;this.modelMaxTokens=4096;this.modelToolUse=true;
    alert('Defaults restored locally. Use Save or slider changes to persist.')},
  async testConfig(){this.testConfigResult='Running selftest...';
    try{const r=await this.api('/selftest',{method:'POST'});
      this.testConfigResult=r.ok?'All checks passed':'Some checks failed';console.log('selftest:',r)}catch(e){this.testConfigResult='Error: '+e.message}},
  async loadDocuments(){
    try{let url='/api/docs/?limit=20&offset='+this.docsOffset;
      if(this.docSearchFilter)url+='&file_type='+encodeURIComponent(this.docSearchFilter);
      const d=await this.api(url);this.docsList=d.items||[];this.docsTotal=d.total||0}catch(e){this.docsList=[];this.docsTotal=0}},
  async deleteDoc(id){if(!confirm('Delete this document?'))return;
    try{await this.api('/api/docs/'+id,{method:'DELETE'});await this.loadDocuments()}catch(e){alert('Error: '+e.message)}},
  async handleDocDrop(e){const files=e.dataTransfer?.files;if(!files?.length)return;for(const f of files)await this.uploadDoc(f)},
  handleDocFile(e){const files=e.target.files;if(!files?.length)return;for(const f of files)this.uploadDoc(f);e.target.value=''},
  async uploadDoc(file){this.uploadStatus='Uploading '+file.name+'...';
    this.ingestPipeline={active:true,steps:[{name:'UPLOAD',status:'active'},{name:'CHUNK',status:''},{name:'EMBED',status:''},{name:'STORE',status:''},{name:'SYNC',status:''}],detail:file.name};
    try{const r=await this.api('/api/docs/ingest',{method:'POST',body:{file_path:file.name}});
      this.uploadStatus=r.status==='error'?'Error: '+(r.error_message||'failed'):'Ingested: '+file.name;
      this.ingestPipeline.steps.forEach(s=>s.status='done');setTimeout(()=>{this.ingestPipeline.active=false},3000);
      await this.loadDocuments()}catch(e){this.uploadStatus='Error: '+e.message;this.ingestPipeline.active=false}},
  async downloadLogs(){const blob=new Blob([JSON.stringify(this.auditLogs,null,2)],{type:'application/json'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='nanobot-logs.json';a.click()},
  async clearChatSession(){this.chatMessages=[];this.chatStreamText='';this.chatSessionId='';this.chatSources=[];this.pipelineSteps=[];this.chatInternals=[];this.chatTraceSteps=[]},

  // === Monitoring ===
  async loadMonitoring(){
    try{const[snap,hist,hm,al]=await Promise.all([
      this.api('/api/metrics/snapshot'),this.api('/api/metrics/history'),
      this.api('/api/metrics/heatmap'),this.api('/api/metrics/alerts')]);
      this.monitorSnap=snap;this.monitorHeatmap=hm.slots||[];this.monitorAlerts=al.alerts||[];
      this.$nextTick(()=>this.updateResourceChart(hist))}catch(e){console.error('loadMonitoring:',e)}},
  updateResourceChart(hist){
    const ctx=document.getElementById('resourceHistoryChart');
    if(!ctx)return;if(this.resourceChart)this.resourceChart.destroy();
    const cpuData=hist.cpu||[];const ramData=hist.ram||[];
    const labels=cpuData.map(d=>d.t?.substring(11,16)||'');
    this.resourceChart=new Chart(ctx,{type:'line',data:{labels,datasets:[
      {label:'CPU %',data:cpuData.map(d=>d.v),borderColor:'rgba(138,76,252,.8)',backgroundColor:'rgba(138,76,252,.1)',fill:true,tension:.3},
      {label:'RAM %',data:ramData.map(d=>d.v),borderColor:'rgba(83,221,252,.8)',backgroundColor:'rgba(83,221,252,.1)',fill:true,tension:.3}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#a3aac4',font:{family:'Space Grotesk'}}}},
      scales:{x:{ticks:{color:'#a3aac4',font:{size:10}},grid:{color:'rgba(64,72,93,.1)'}},
      y:{min:0,max:100,ticks:{color:'#a3aac4',callback:v=>v+'%'},grid:{color:'rgba(64,72,93,.1)'}}}}})},
  formatUptime(s){if(!s)return'—';const d=Math.floor(s/86400);const h=Math.floor((s%86400)/3600);const m=Math.floor((s%3600)/60);
    return(d?d+'d ':'')+(h?h+'h ':'')+(m?m+'m':'')},

  // === Most Used Tools Chart ===
  async loadMostUsedTools(){
    try{const ts=await this.api('/token-stats');this.mostUsedTools=ts.by_endpoint||[];
      this.$nextTick(()=>this.updateMostUsedChart())}catch(e){console.error(e)}},
  updateMostUsedChart(){
    const ctx=document.getElementById('mostUsedChart');
    if(!ctx||!this.mostUsedTools.length)return;if(this.mostUsedChart)this.mostUsedChart.destroy();
    const top=this.mostUsedTools.slice(0,8);
    this.mostUsedChart=new Chart(ctx,{type:'bar',data:{
      labels:top.map(t=>t.endpoint),datasets:[{label:'Calls',data:top.map(t=>t.calls),
      backgroundColor:'rgba(138,76,252,.6)',borderRadius:4}]},
      options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{ticks:{color:'#a3aac4'},grid:{color:'rgba(64,72,93,.1)'}},
      y:{ticks:{color:'#a3aac4',font:{family:'JetBrains Mono',size:10}},grid:{display:false}}}}})},
  generateAdvice(ts){
    if(!ts||!ts.by_endpoint)return;this.agentAdvice='';
    const total=ts.total_calls||0;const cost=ts.total_cost_usd||0;
    const byEp=ts.by_endpoint||[];
    if(cost>5)this.agentAdvice='High daily cost ($'+cost.toFixed(2)+'). Consider switching lower-cost models for embedding tasks.';
    else if(total>500){const topEp=byEp[0];if(topEp&&topEp.calls>total*0.5)this.agentAdvice=topEp.endpoint+' accounts for '+Math.round(topEp.calls/total*100)+'% of all calls. Consider caching or batching these requests.'}
    else if(byEp.length===1)this.agentAdvice='All traffic goes to a single endpoint. Consider diversifying routing for resilience.'},

  // === Trust (v10) ===
  async loadTrust(){
    try{const[p,a]=await Promise.all([this.api('/trust/policies'),this.api('/trust/audit?limit=50')]);
      this.trustPolicies=p.policies||[];this.trustAudit=a.entries||[]}catch(e){console.error('loadTrust:',e)}},
  async updateTrust(type,level){
    try{await this.api('/trust/policies/'+type,{method:'POST',body:{trust_level:level}});await this.loadTrust()}catch(e){alert('Error: '+e.message)}},
  async promoteTrust(type){
    try{await this.api('/trust/promote/'+type,{method:'POST',body:{}});await this.loadTrust()}catch(e){alert('Error: '+e.message)}},
  async updateAutoPromote(type,threshold){
    try{await this.api('/trust/policies/'+type,{method:'POST',body:{auto_promote_after:threshold}});await this.loadTrust()}catch(e){alert('Error: '+e.message)}},
  async cancelAudit(id){
    if(!confirm('Cancel this pending action?'))return;
    try{await this.api('/trust/cancel/'+id,{method:'POST',body:{}});await this.loadTrust()}catch(e){alert('Error: '+e.message)}},

  // === Costs (v10) ===
  async loadCosts(){
    try{const[b,r,h]=await Promise.all([this.api('/budget/status'),this.api('/budget/daily-report'),this.api('/budget/history')]);
      this.costData=b;this.costByModel=r.by_model||[];
      this.costHistory=Array.isArray(h)?h:(h.days||h.history||[]);
      this.$nextTick(()=>this.updateCostHistoryChart());
    }catch(e){console.error('loadCosts:',e)}},
  updateCostHistoryChart(){
    const ctx=document.getElementById('costHistoryChart');
    if(!ctx||!this.costHistory.length)return;
    if(this.costHistoryChart)this.costHistoryChart.destroy();
    const labels=this.costHistory.map(d=>d.date||d.day||'');
    const costs=this.costHistory.map(d=>(d.cost_cents||d.total_cost_cents||0)/100);
    const tokens=this.costHistory.map(d=>d.total_tokens||d.tokens||0);
    this.costHistoryChart=new Chart(ctx,{type:'bar',data:{labels,datasets:[
      {label:'Cost ($)',data:costs,backgroundColor:'rgba(138,76,252,.6)',borderRadius:4,yAxisID:'y'},
      {label:'Tokens',data:tokens,backgroundColor:'rgba(83,221,252,.3)',borderRadius:4,yAxisID:'y1'}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#a3aac4',font:{family:'Space Grotesk'}}}},
      scales:{x:{ticks:{color:'#a3aac4',font:{family:'JetBrains Mono',size:10}},grid:{color:'rgba(64,72,93,.1)'}},
      y:{position:'left',ticks:{color:'#a3aac4',callback:v=>'$'+v},grid:{color:'rgba(64,72,93,.1)'}},
      y1:{position:'right',ticks:{color:'#a3aac4',callback:v=>v>=1000?(v/1000)+'k':v},grid:{drawOnChartArea:false}}}}});
  },

  // === Workflows (v10) ===
  async loadWorkflows(){
    try{const d=await this.api('/workflows');this.workflows=d.workflows||[]}catch(e){console.error('loadWorkflows:',e)}},
  async toggleWorkflow(id,enabled){
    try{await this.api('/workflows/'+id+'/toggle',{method:'POST',body:{auto_suggest:enabled}});await this.loadWorkflows()}catch(e){alert('Error: '+e.message)}},
  async deleteWorkflow(id){
    if(!confirm('Delete this workflow?'))return;
    try{await this.api('/workflows/'+id,{method:'DELETE'});await this.loadWorkflows()}catch(e){alert('Error: '+e.message)}},

  // === Agents (v10) ===
  async loadAgents(){
    try{const[s,h]=await Promise.all([this.api('/agent/status'),this.api('/agent/history')]);
      this.agentList=s.agents||[];this.agentHistory=h.executions||[]}catch(e){console.error('loadAgents:',e)}},
  estimateAgentCost(tokens){
    if(!tokens)return'\u2014';
    const cost=(tokens/1000)*0.003;
    return'~$'+cost.toFixed(3);
  }
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
# Mobile PWA CSS
# ---------------------------------------------------------------------------
MOBILE_CSS = """
/* ===== Mobile PWA View (< 768px) ===== */
@media (max-width: 767px) {
  .topnav { display: none !important; }
  main { display: none !important; }
  #mobile-chat-view { display: flex !important; }
}
@media (min-width: 768px) {
  #mobile-chat-view { display: none !important; }
}

/* Mobile chat layout */
#mobile-chat-view {
  display: none;
  flex-direction: column;
  height: 100dvh;
  height: 100vh;
  background: var(--bg);
  position: fixed;
  inset: 0;
  z-index: 200;
}

/* Mobile header */
.mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.mobile-header .brand { font-weight: 700; font-size: 16px; color: var(--cyan); }
.mobile-header-actions { display: flex; gap: 8px; }

/* Message list */
.mobile-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  -webkit-overflow-scrolling: touch;
}

/* Message bubbles */
.message-bubble {
  max-width: 80%;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
}
.message-bubble.user {
  align-self: flex-end;
  background: var(--gradient-cta);
  border-radius: 18px 18px 4px 18px;
  color: #fff;
  margin-left: auto;
}
.message-bubble.assistant {
  align-self: flex-start;
  background: var(--surface-container);
  border-radius: 18px 18px 18px 4px;
  color: var(--on-surface);
}

/* Typing indicator */
.typing-indicator { display: flex; gap: 4px; align-items: center; padding: 4px 0; }
.typing-indicator span {
  width: 6px; height: 6px; background: var(--muted);
  border-radius: 50%; animation: typing-bounce 1.2s infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

/* Quick actions */
.mobile-quick-actions {
  display: flex;
  gap: 8px;
  padding: 8px 16px;
  overflow-x: auto;
  flex-shrink: 0;
  border-top: 1px solid var(--border);
}
.mobile-quick-actions button {
  white-space: nowrap;
  flex-shrink: 0;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  color: var(--text);
  padding: 6px 14px;
  font-size: 12px;
  cursor: pointer;
}

/* Input bar */
.mobile-input-bar {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 16px;
  background: var(--card);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
.mobile-input-bar textarea {
  flex: 1;
  min-height: 40px;
  max-height: 120px;
  resize: none;
  border-radius: 20px;
  padding: 10px 14px;
  font-size: 14px;
  overflow-y: auto;
}
.mobile-input-bar .btn-send {
  height: 40px;
  width: 40px;
  border-radius: 50%;
  background: var(--blue);
  border: none;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.mobile-input-bar .btn-mic {
  height: 40px;
  width: 40px;
  border-radius: 50%;
  background: var(--border);
  border: none;
  color: var(--text);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.mobile-input-bar .btn-mic.recording {
  background: var(--red);
  animation: recording-pulse 1s infinite;
}
@keyframes recording-pulse {
  0%, 100% { opacity: 1; } 50% { opacity: 0.5; }
}

/* Install banner */
.install-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--card);
  border-bottom: 1px solid var(--blue);
  font-size: 13px;
  flex-shrink: 0;
}
.install-banner span { flex: 1; }
.install-banner button {
  background: var(--blue);
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.install-banner .btn-dismiss {
  background: transparent;
  color: var(--muted);
  font-size: 16px;
}
"""

# ---------------------------------------------------------------------------
# Mobile Chat HTML
# ---------------------------------------------------------------------------
MOBILE_CHAT_HTML = """
<div id="mobile-chat-view"
     x-data="mobileChatApp()"
     x-init="init()">

  <!-- Install banner (Chrome Android A2HS) -->
  <div x-show="showInstallBanner" class="install-banner">
    <span>Installer Nanobot sur l'ecran d'accueil</span>
    <button @click="installApp()">Installer</button>
    <button class="btn-dismiss" @click="showInstallBanner = false">X</button>
  </div>

  <!-- Header -->
  <div class="mobile-header">
    <span class="brand">Nanobot</span>
    <div class="mobile-header-actions">
      <button class="btn btn-muted btn-sm" @click="newConversation()">+ Nouveau</button>
      <template x-if="pushEnabled">
        <button class="btn btn-sm"
                :class="pushSubscribed ? 'btn-blue' : 'btn-muted'"
                @click="pushSubscribed ? unsubscribePush() : subscribePush()"
                :title="pushSubscribed ? 'Desactiver les notifications' : 'Activer les notifications'">
          Bell
        </button>
      </template>
    </div>
  </div>

  <!-- Messages -->
  <div class="mobile-messages" x-ref="messagesContainer">
    <template x-for="msg in messages" :key="msg.ts">
      <div class="message-bubble" :class="msg.role">
        <template x-if="msg.streaming && msg.content === ''">
          <div class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </template>
        <template x-if="!(msg.streaming && msg.content === '')">
          <div x-html="renderMarkdown(msg.content)"></div>
        </template>
      </div>
    </template>
  </div>

  <!-- Quick actions -->
  <div class="mobile-quick-actions">
    <button @click="triggerBriefing()">Briefing maintenant</button>
  </div>

  <!-- Input bar -->
  <div class="mobile-input-bar">
    <textarea x-model="inputText"
              placeholder="Message..."
              rows="1"
              @keydown.enter.prevent="if(!$event.shiftKey) sendMessage()"
              @input="autoResize($event.target)"></textarea>
    <template x-if="voiceEnabled">
      <button class="btn-mic" :class="{ recording: isRecording }"
              @click="startVoiceInput()"
              :title="isRecording ? 'Arreter' : 'Microphone'">
        Mic
      </button>
    </template>
    <button class="btn-send"
            @click="sendMessage(); navigator.vibrate && navigator.vibrate(10)"
            :disabled="isStreaming || !inputText.trim()"
            title="Envoyer">
      &gt;
    </button>
  </div>
</div>
"""

# ---------------------------------------------------------------------------
# Mobile Chat JS
# ---------------------------------------------------------------------------
MOBILE_CHAT_JS = """
function mobileChatApp() {
  return {
    messages: [],
    inputText: '',
    isStreaming: false,
    isRecording: false,
    showInstallBanner: false,
    deferredPrompt: null,
    pushEnabled: false,
    pushSubscribed: false,
    voiceEnabled: false,

    async init() {
      await this.loadHistory();
      this.checkPushAvailability();
      this.checkVoiceAvailability();
      window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        this.deferredPrompt = e;
        this.showInstallBanner = true;
      });
    },

    async loadHistory() {
      try {
        const r = await fetch('/api/chat/history?limit=20');
        if (r.ok) {
          const data = await r.json();
          this.messages = (data.messages || []).map(m => ({
            role: m.role, content: m.content,
            ts: m.ts || Date.now(), streaming: false
          }));
          this.$nextTick(() => this.scrollToBottom());
        }
      } catch(e) { console.warn('[Mobile] loadHistory failed', e); }
    },

    async checkPushAvailability() {
      try {
        const r = await fetch('/api/push/vapid-public-key');
        if (r.ok) {
          this.pushEnabled = true;
          await this.checkPushSubscription();
        }
      } catch(e) {}
    },

    async checkVoiceAvailability() {
      try {
        const r = await fetch('/api/voice/status');
        if (r.ok) { const d = await r.json(); this.voiceEnabled = d.enabled === true; }
      } catch(e) {}
    },

    async sendMessage() {
      const text = this.inputText.trim();
      if (!text || this.isStreaming) return;
      this.inputText = '';
      this.messages.push({ role: 'user', content: text, ts: Date.now(), streaming: false });
      const assistantMsg = { role: 'assistant', content: '', ts: Date.now(), streaming: true };
      this.messages.push(assistantMsg);
      this.isStreaming = true;
      this.$nextTick(() => this.scrollToBottom());
      try {
        const es = new EventSource('/api/chat/stream?message=' + encodeURIComponent(text));
        es.onmessage = (e) => {
          if (e.data === '[DONE]') { assistantMsg.streaming = false; es.close(); this.isStreaming = false; return; }
          try { const d = JSON.parse(e.data); assistantMsg.content += d.content || ''; } catch(_) {}
          this.$nextTick(() => this.scrollToBottom());
        };
        es.onerror = () => { es.close(); this.isStreaming = false; assistantMsg.streaming = false; };
      } catch(e) { this.isStreaming = false; assistantMsg.streaming = false; }
    },

    async newConversation() {
      try { await fetch('/api/chat/reset', { method: 'POST' }); } catch(e) {}
      this.messages = [];
    },

    async triggerBriefing() {
      try {
        await fetch('/api/scheduler/trigger-briefing', { method: 'POST' });
        this.messages.push({ role: 'assistant', content: 'Briefing declenche.', ts: Date.now(), streaming: false });
      } catch(e) {}
    },

    async installApp() {
      if (!this.deferredPrompt) return;
      this.deferredPrompt.prompt();
      await this.deferredPrompt.userChoice;
      this.showInstallBanner = false;
      this.deferredPrompt = null;
    },

    async checkPushSubscription() {
      try {
        if (!window._swRegistration) return;
        const sub = await window._swRegistration.pushManager.getSubscription();
        this.pushSubscribed = sub !== null;
      } catch(e) {}
    },

    async subscribePush() {
      try {
        const r = await fetch('/api/push/vapid-public-key');
        const { vapid_public_key } = await r.json();
        const appKey = this._urlBase64ToUint8Array(vapid_public_key);
        const sub = await window._swRegistration.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: appKey
        });
        const keys = sub.toJSON().keys;
        await fetch('/api/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: sub.endpoint, p256dh: keys.p256dh, auth: keys.auth })
        });
        this.pushSubscribed = true;
      } catch(e) { console.warn('[Mobile] subscribePush failed', e); }
    },

    async unsubscribePush() {
      try {
        const sub = await window._swRegistration.pushManager.getSubscription();
        if (sub) {
          await fetch('/api/push/unsubscribe', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: sub.endpoint })
          });
          await sub.unsubscribe();
        }
        this.pushSubscribed = false;
      } catch(e) { console.warn('[Mobile] unsubscribePush failed', e); }
    },

    async startVoiceInput() {
      if (this.isRecording) return;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        const chunks = [];
        this.isRecording = true;
        recorder.ondataavailable = e => chunks.push(e.data);
        recorder.onstop = async () => {
          this.isRecording = false;
          stream.getTracks().forEach(t => t.stop());
          const blob = new Blob(chunks, { type: 'audio/webm' });
          const fd = new FormData(); fd.append('audio', blob, 'voice.webm');
          try {
            const r = await fetch('/api/voice/chat', { method: 'POST', body: fd });
            if (r.ok) {
              const d = await r.json();
              if (d.transcription) this.messages.push({ role: 'user', content: d.transcription, ts: Date.now(), streaming: false });
              if (d.response) this.messages.push({ role: 'assistant', content: d.response, ts: Date.now(), streaming: false });
              this.$nextTick(() => this.scrollToBottom());
            }
          } catch(e) { console.warn('[Mobile] voice upload failed', e); }
        };
        recorder.start();
        setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 30000);
        document.querySelector('.btn-mic').addEventListener('click', () => {
          if (recorder.state === 'recording') recorder.stop();
        }, { once: true });
      } catch(e) { this.isRecording = false; console.warn('[Mobile] getUserMedia failed', e); }
    },

    renderMarkdown(text) {
      if (typeof marked !== 'undefined') return marked.parse(text || '');
      return (text || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\\\n/g,'<br>');
    },

    scrollToBottom() {
      const el = this.$refs.messagesContainer;
      if (el) el.scrollTop = el.scrollHeight;
    },

    autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    },

    _urlBase64ToUint8Array(base64String) {
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const rawData = atob(base64);
      return new Uint8Array([...rawData].map(c => c.charCodeAt(0)));
    },
  };
}
"""

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def build_admin_html() -> str:
    """Assemble the full admin SPA HTML."""
    pwa_head = ""
    pwa_script = ""
    pwa_enabled = os.getenv("PWA_ENABLED", "true").lower() == "true"
    if pwa_enabled:
        pwa_head = """
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1a1a2e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Nanobot">
<link rel="apple-touch-icon" href="/static/icons/icon-192.png">"""
        pwa_script = """
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    try {
      const reg = await navigator.serviceWorker.register('/static/sw.js', { scope: '/' });
      console.log('[Nanobot PWA] Service Worker enregistre', reg.scope);
      window._swRegistration = reg;
    } catch (err) {
      console.warn('[Nanobot PWA] Enregistrement SW echoue', err);
    }
  });
}
</script>"""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>nanobot admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
{pwa_head}
<style>{ADMIN_CSS}{MOBILE_CSS}</style>
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
{SECTION_MONITORING}
{SECTION_ADVANCED}
{SECTION_SCHEDULER}
</main>
{MOBILE_CHAT_HTML}
<script>{ADMIN_JS}</script>
<script>{MOBILE_CHAT_JS}</script>
{pwa_script}
</body></html>"""
