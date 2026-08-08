// PCBGenius Kanban — Build Health Monitor + Agent Status
// Agents POST status to /api/update, Kanban serves dashboard at /
// RED flags = critical issues that need user attention

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event))
})

async function handleRequest(event) {
  const request = event.request
  const url = new URL(request.url)
  const method = request.method

  // ── API: Agent/supervisor posts status update ──
  if (url.pathname === '/api/update' && method === 'POST') {
    try {
      const body = await request.json()
      const key = `agent:${body.agent || 'unknown'}`
      await KANBAN_KV.put(key, JSON.stringify({
        ...body,
        timestamp: body.timestamp || Date.now(),
        updated: new Date().toISOString()
      }))
      if (body.status === 'critical') {
        const crit = JSON.parse(await KANBAN_KV.get('critical:list') || '[]')
        crit.push({ agent: body.agent, feature: body.feature, message: body.message, time: new Date().toISOString() })
        await KANBAN_KV.put('critical:list', JSON.stringify(crit.slice(-20)))
      }
      return new Response(JSON.stringify({ ok: true }), {
        headers: { 'content-type': 'application/json' }
      })
    } catch(e) {
      return new Response(JSON.stringify({ ok: false, error: e.message }), {
        status: 400, headers: { 'content-type': 'application/json' }
      })
    }
  }

  if (url.pathname === '/api/agents' && method === 'GET') {
    const agents = []
    let cursor
    do {
      const result = await KANBAN_KV.list({ prefix: 'agent:', cursor })
      for (const key of result.keys) {
        const val = JSON.parse(await KANBAN_KV.get(key.name))
        agents.push(val)
      }
      cursor = result.cursor
    } while (cursor)
    return new Response(JSON.stringify(agents), {
      headers: { 'content-type': 'application/json' }
    })
  }

  return serveDashboard()
}

async function serveDashboard() {
  // Gather all agent statuses from KV
  const agents = []
  let cursor
  do {
    const result = await KANBAN_KV.list({ prefix: 'agent:', cursor })
    for (const key of result.keys) {
      const val = JSON.parse(await KANBAN_KV.get(key.name))
      agents.push(val)
    }
    cursor = result.cursor
  } while (cursor)

  // Get critical alerts
  const criticalList = JSON.parse(await KANBAN_KV.get('critical:list') || '[]')

  // Get pipeline state from R2
  let stage = "0", spend = "0"
  try {
    const stateObj = await PIPELINE_BUCKET.get("state/pipeline_state.txt")
    if (stateObj) stage = await stateObj.text()
  } catch(e) {}
  try {
    const costObj = await PIPELINE_BUCKET.get("state/.cost_ledger")
    if (costObj) spend = await costObj.text()
  } catch(e) {}
  const stageNum = parseInt(stage) || 0
  const spendFloat = parseFloat(spend) || 0
  const cap = 90
  const pct = Math.min(100, Math.round((spendFloat / cap) * 100))

  // Count statuses — treat ok/warning/active as RUNNING (live heartbeat),
  // done as completed, failed/critical as failed. Supervisor posts "ok".
  const RUNNING_STATES = ['running','ok','warning','active']
  const running = agents.filter(a => RUNNING_STATES.includes(a.status)).length
  const completed = agents.filter(a => a.status === 'done').length
  const failed = agents.filter(a => a.status === 'failed' || a.status === 'critical').length
  const totalAgents = agents.length
  // latest update for "last seen"
  const lastSeen = agents.reduce((acc,a) => {
    const t = new Date(a.updated || a.timestamp || 0).getTime()
    return t > acc ? t : acc
  }, 0)

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PCBGenius — Build Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0d1117; color: #e6edf3; padding: 20px; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; flex-wrap: wrap; gap: 10px; }
  h1 { font-size: 20px; font-weight: 700; }
  .badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .badge.green { background: #3fb950; color: #0d1117; }
  .badge.red { background: #f85149; color: #0d1117; }
  .badge.yellow { background: #d29922; color: #0d1117; }

  .stats { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 16px; min-width: 110px; flex: 1; }
  .stat .num { font-size: 22px; font-weight: 700; }
  .stat .lbl { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .5px; }
  .stat.green .num { color: #3fb950; } .stat.blue .num { color: #58a6ff; }
  .stat.yellow .num { color: #d29922; } .stat.red .num { color: #f85149; }

  .services { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
  .svc { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 16px; min-width: 150px; flex: 1; }
  .svc .name { font-size: 12px; color: #8b949e; }
  .svc .val { font-size: 16px; font-weight: 600; margin-top: 4px; }
  .svc .val.green { color: #3fb950; } .svc .val.red { color: #f85149; }
  .svc .val.yellow { color: #d29922; }

  .board { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }
  .col { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 12px; min-height: 150px; }
  .col h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .5px; color: #8b949e; margin-bottom: 12px; }
  .card { background: #21262d; border: 1px solid #30363d; border-left: 3px solid #58a6ff; border-radius: 6px; padding: 10px; margin-bottom: 10px; font-size: 13px; }
  .card .title { font-weight: 600; font-size: 14px; }
  .card .sub { font-size: 11px; color: #8b949e; margin-top: 4px; }
  .card.done { border-left-color: #3fb950; opacity: 0.7; }
  .card.active { border-left-color: #d29922; }
  .card.failed { border-left-color: #f85149; }
  .card.critical { border-left-color: #f85149; background: #2d1517; border-color: #f85149; }
  .card.pending { border-left-color: #30363d; }

  .critical-banner { background: #2d1517; border: 1px solid #f85149; border-radius: 8px; padding: 12px 16px; margin-bottom: 18px; }
  .critical-banner h3 { color: #f85149; font-size: 14px; margin-bottom: 6px; }
  .critical-banner .item { font-size: 13px; margin: 4px 0; padding: 4px 0; border-bottom: 1px solid #30363d; }
  .critical-banner .item:last-child { border-bottom: none; }

  .bar { height: 5px; background: #30363d; border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .bar > div { height: 100%; border-radius: 3px; }
  .bar > div.green { background: #3fb950; }
  .bar > div.yellow { background: #d29922; }
  .bar > div.red { background: #f85149; }

  .pulse { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #3fb950; margin-right: 6px; animation: p 1.5s infinite; }
  @keyframes p { 0%,100%{opacity:1} 50%{opacity:.3} }

  footer { margin-top: 20px; font-size: 12px; color: #8b949e; text-align: center; }

  @media (max-width: 900px) { .board { grid-template-columns: repeat(2,1fr); } }
  @media (max-width: 540px) { .board { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<header>
  <h1><span class="pulse"></span>PCBGenius — Build Monitor</h1>
  <div style="display:flex;gap:8px;align-items:center">
    ${criticalList.length > 0
      ? '<span class="badge red">🔴 ${criticalList.length} CRITICAL</span>'
      : '<span class="badge green">✅ All Good</span>'}
    <span style="font-size:12px;color:#8b949e">${new Date().toISOString().slice(0,19).replace('T',' ')}</span>
  </div>
</header>

${criticalList.length > 0 ? `
<div class="critical-banner">
  <h3>🔴 CRITICAL ALERTS — Needs Attention</h3>
  ${criticalList.map(c => `<div class="item"><b>${c.agent}</b> — ${c.feature}: ${c.message} <span style="color:#8b949e;font-size:11px">${c.time.slice(0,19)}</span></div>`).join('')}
</div>` : ''}

<div class="services">
  <div class="svc"><div class="name">🏗 Pipeline Stage</div><div class="val blue">Stage ${stageNum}/6</div></div>
  <div class="svc"><div class="name">💰 Spend</div><div class="val yellow">$${spendFloat.toFixed(2)} / $${cap}</div></div>
  <div class="svc"><div class="name">🤖 Agents Live</div><div class="val green">${running} active</div></div>
  <div class="svc"><div class="name">✅ Done</div><div class="val green">${completed}</div></div>
  <div class="svc"><div class="name">⏱ Last Update</div><div class="val">${lastSeen ? new Date(lastSeen).toUTCString().slice(17,25)+' UTC' : '—'}</div></div>
</div>

<div class="stats">
  <div class="stat blue"><div class="num">${totalAgents}</div><div class="lbl">Total Agents</div></div>
  <div class="stat green"><div class="num">${running}</div><div class="lbl">Running</div></div>
  <div class="stat"><div class="num">${completed}</div><div class="lbl">Completed</div></div>
  <div class="stat ${failed > 0 ? 'red' : ''}"><div class="num">${failed}</div><div class="lbl">Failed</div></div>
  <div class="stat yellow"><div class="num">${stageNum}/6</div><div class="lbl">Pipeline Stage</div></div>
  <div class="stat"><div class="num">$${spendFloat.toFixed(2)}</div><div class="lbl">Spend / $${cap}</div></div>
</div>

<div class="board">
  <div class="col">
    <h2>🟢 Running (${running})</h2>
    ${agents.filter(a => RUNNING_STATES.includes(a.status)).map(a => `
      <div class="card active">
        <div class="title">${a.agent}</div>
        <div class="sub">${a.feature || 'task'} — ${a.status}</div>
        ${a.message ? `<div class="sub">${a.message}</div>` : ''}
        ${a.updated ? `<div class="sub" style="color:#8b949e;font-size:10px">${String(a.updated).slice(0,19)}</div>` : ''}
      </div>
    `).join('') || '<div style="color:#8b949e;font-size:12px">No agents running</div>'}
  </div>
  <div class="col">
    <h2>✅ Completed (${completed})</h2>
    ${agents.filter(a => a.status === 'done').map(a => `
      <div class="card done">
        <div class="title">${a.agent}</div>
        <div class="sub">${a.feature}</div>
      </div>
    `).join('') || '<div style="color:#8b949e;font-size:12px">None yet</div>'}
  </div>
  <div class="col">
    <h2>${failed > 0 ? '🔴 Failed' : '⏳ Pending'}</h2>
    ${agents.filter(a => a.status === 'failed' || a.status === 'critical').map(a => `
      <div class="card ${a.status === 'critical' ? 'critical' : 'failed'}">
        <div class="title">${a.agent}</div>
        <div class="sub">${a.feature}</div>
        <div class="sub" style="color:#f85149">${a.message || 'Error'}</div>
      </div>
    `).join('')}
    <div style="color:#8b949e;font-size:12px;margin-top:8px">Next features will launch when current level completes</div>
  </div>
</div>

<div class="board" style="grid-template-columns:1fr">
  <div class="col">
    <h2>📋 Feature Queue (by level)</h2>
    <div style="font-size:12px;color:#8b949e;margin-bottom:8px;display:flex;gap:16px;flex-wrap:wrap">
      <span><b>Level A</b>: ${completed >= 5 ? '✅' : '🔄'} 4A Canvas · 4B Backend · A3 KiCad · Checkpoint · Versioning</span>
      <span><b>Level B</b>: ${completed >= 8 ? '✅' : '⏳'} atopile · pcbflow · Tauri</span>
      <span><b>Level C</b>: ${completed >= 11 ? '✅' : '⏳'} Auto-layout · Multi-layer · NL Diffs · Repair</span>
      <span><b>Level D</b>: ${completed >= 15 ? '✅' : '⏳'} 16 features</span>
      <span><b>Level E</b>: ${completed >= 31 ? '✅' : '⏳'} Moat · Physics · 100 MCUs</span>
      <span><b>💰 Budget</b>: $${spendFloat.toFixed(2)} spent · $${(cap - spendFloat).toFixed(2)} remaining</span>
    </div>
    <div class="bar"><div class="green" style="width:${pct}%"></div></div>
  </div>
</div>

<footer>
  PCBGenius · Auto-refresh every 30s ·
  <span style="color:#8b949e">⬆️ Agents POST to /api/update · ⬇️ This dashboard reads from KV</span>
  <br>
  <span style="font-size:10px">🔴 = needs your attention · 🟡 = auto-fixing · 🟢 = all good</span>
</footer>

<script>
  // Auto-refresh every 30 seconds
  setTimeout(() => location.reload(), 30000)
</script>
</body>
</html>`

  return new Response(html, {
    headers: { "content-type": "text/html;charset=UTF-8" }
  })
}