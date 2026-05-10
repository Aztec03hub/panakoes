// Shared shell injector. Keeps each page DRY.
// Usage: <body><div id="app-root" data-active="overview"></div><script src="_shell.js"></script><script>injectShell({title: 'OVERVIEW', active: 'F1', main: `...html...`});</script>
function injectShell(opts) {
  const active = opts.active || '';
  const subtitle = opts.subtitle || '';
  const fkeys = [
    ['F1', 'OVERVIEW', 'overview.html'],
    ['F2', 'COST/SVC', 'cost-by-service.html'],
    ['F3', 'COST/TEN', 'cost-by-tenant.html'],
    ['F4', 'ANOMALIES', 'cost-anomalies.html'],
    ['F5', 'LIFECYCLE', 'lifecycle.html'],
    ['F6', 'AUDIT', 'audit-log.html'],
  ];
  const fkeyHtml = fkeys.map(([k, label, href]) =>
    `<a class="fkey ${active === k ? 'active' : ''}" href="${href}"><span class="k">${k}</span>${label}</a>`
  ).join('');

  const navItems = (active) => `
    <div class="group">
      <div class="group-h">Tier 1 · Health</div>
      <a class="nav-item ${active==='overview'?'active':''}" href="overview.html"><span>Overview</span><span class="kbd">F1</span></a>
    </div>
    <div class="group">
      <div class="group-h">Tier 2 · Cost</div>
      <a class="nav-item ${active==='cost-svc'?'active':''}" href="cost-by-service.html"><span>By service</span><span class="kbd">F2</span></a>
      <a class="nav-item ${active==='cost-ten'?'active':''}" href="cost-by-tenant.html"><span>By tenant</span><span class="kbd">F3</span></a>
      <a class="nav-item ${active==='anomalies'?'active':''}" href="cost-anomalies.html"><span>Anomalies</span><span class="badge">3</span></a>
      <a class="nav-item" href="#"><span>Forecast</span><span class="kbd">F7</span></a>
    </div>
    <div class="group">
      <div class="group-h">Tier 3 · Ops</div>
      <a class="nav-item ${active==='lifecycle'?'active':''}" href="lifecycle.html"><span>Lifecycle</span><span class="badge amber">⚠</span></a>
      <a class="nav-item ${active==='audit'?'active':''}" href="audit-log.html"><span>Audit log</span><span class="kbd">F6</span></a>
    </div>
    <div class="group">
      <div class="group-h">Index</div>
      <a class="nav-item ${active==='index'?'active':''}" href="index.html"><span>Index</span><span class="kbd">F0</span></a>
    </div>`;

  const root = document.getElementById('app-root');
  root.outerHTML = `
    <div class="app">
      <div class="topstrip">
        <span class="brand">PNKS&nbsp;ADMIN</span>
        <span class="variant-tag">VARIANT 4 / 5 — BLOOMBERG-DENSE</span>
        <span class="sep">|</span><span>v0.1.0-dev</span>
        <span class="sep">|</span><span>region us-east-1</span>
        <span class="sep">|</span><span>account 659225405128</span>
        <span class="sep">|</span><span>env <span class="warn">dev</span></span>
        <span class="sep">|</span><span class="bright">${opts.title || ''}</span>
        ${subtitle ? `<span class="sep">|</span><span class="dim">${subtitle}</span>` : ''}
        <span class="clock"><span class="blink">●</span> LIVE&nbsp;&nbsp;<span id="clk">2026-05-09 14:23:07 UTC</span></span>
      </div>
      <div class="topbar">
        <div class="cmd-input"><span class="prompt">PNKS&gt;</span><input type="text" placeholder="command..." /></div>
        ${fkeyHtml}
        <div class="meta">
          <div class="pair"><span class="lbl">USER</span><span class="val">phil@lafayettelabs.com</span></div>
          <div class="pair"><span class="lbl">ROLE</span><span class="val">admin</span></div>
          <div class="pair"><span class="lbl">MFA</span><span class="val pos">VERIFIED</span></div>
          <div class="pair"><span class="lbl">SESS</span><span class="val">23m12s</span></div>
        </div>
      </div>
      <nav class="side">${navItems(opts.activeNav || '')}</nav>
      <div class="main">${opts.main || ''}</div>
      <div class="status">
        <span class="slot"><span class="dot ok"></span>API GATEWAY</span>
        <span class="slot"><span class="dot ok"></span>RDS</span>
        <span class="slot"><span class="dot ok"></span>DYNAMO</span>
        <span class="slot"><span class="dot warn"></span>BATCH (1 SPOT INTERRUPT)</span>
        <span class="slot"><span class="dot ok"></span>S3</span>
        <span class="slot"><span class="dot err"></span>SUMMARIZATION (1/3 unhealthy)</span>
        <div class="right"><span>BUILD a3f29c1</span><span>TZ UTC</span><span>PRESS ? FOR HELP</span></div>
      </div>
    </div>`;

  // simple live clock to amplify the live-data feel
  setInterval(() => {
    const el = document.getElementById('clk');
    if (!el) return;
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    el.textContent = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
  }, 1000);
}

// reusable inline SVG sparkline generator
function sparkline(values, color, width = 80, height = 18) {
  if (!values || values.length < 2) return '';
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  const pts = values.map((v, i) => `${(i*step).toFixed(1)},${(height - ((v - min)/range)*height).toFixed(1)}`).join(' ');
  const last = values[values.length-1], first = values[0];
  const stroke = color || (last >= first ? 'var(--green)' : 'var(--red)');
  const fill = (last >= first ? 'rgba(24,201,100,0.12)' : 'rgba(255,59,59,0.12)');
  const area = `0,${height} ${pts} ${width},${height}`;
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
    <polyline points="${area}" fill="${fill}" stroke="none"/>
    <polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="1.2"/>
  </svg>`;
}
