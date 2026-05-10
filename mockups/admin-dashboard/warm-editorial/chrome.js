// Shared masthead / nav / colophon for the Panakoes Admin warm-editorial mockup.
// Pure DOM injection so each page stays standalone HTML and runs from file://.
//
// Pages set <body data-current="overview"> (or "cost-service", etc.) and we
// highlight the right folio. The dateline is updated to "today" if the page
// includes <span data-dateline-today></span>.

(function () {
  const NAV = [
    { key: 'overview',    href: 'overview.html',         label: 'Health' },
    { key: 'cost',        href: 'cost-by-service.html',  label: 'Cost',
      group: ['cost-service', 'cost-tenant', 'cost-anomalies'] },
    { key: 'lifecycle',   href: 'lifecycle.html',        label: 'Lifecycle' },
    { key: 'audit',       href: 'audit-log.html',        label: 'Audit Log' },
  ];

  function buildVariantStrip() {
    return `
      <div class="variant-strip">
        <div class="row">
          <span><span class="pill">Variant 02 / 05</span> &nbsp; Warm &middot; Editorial</span>
          <span><a href="index.html">Mockup Index</a> &nbsp;&middot;&nbsp; mockup/admin-warm-editorial</span>
        </div>
      </div>`;
  }

  function buildMasthead() {
    return `
      <header class="masthead">
        <div class="masthead-inner">
          <div class="left">
            <p>Vol. 0, No. 1</p>
            <p>The Operator's Edition</p>
            <p>Region us-east-1</p>
          </div>
          <h1 class="title">
            <span class="small">Lafayette Labs presents</span>
            Panakoes <em>Admin</em>
            <span class="all-hearing">&mdash; The All-Hearing Desk &mdash;</span>
          </h1>
          <div class="right">
            <p class="live">Aggregator live</p>
            <p>Build v0.1.4 &middot; 7c9d3a1</p>
            <p>Filed from CloudFront edge IAD-2</p>
          </div>
        </div>
      </header>`;
  }

  function buildFolioNav(currentKey) {
    const items = NAV.map(n => {
      const matches = n.key === currentKey || (n.group && n.group.includes(currentKey));
      return `<li><a class="${matches ? 'current' : ''}" href="${n.href}">${n.label}</a></li>`;
    }).join('');
    return `
      <nav class="folio-nav">
        <div class="folio-nav-inner">
          <ul>${items}</ul>
          <div class="who">
            <span>Signed in as</span>
            <span class="actor">phil@lafayettelabs.com</span>
            <span class="ghost">|</span>
            <span>SSO &middot; Better-Auth</span>
            <button class="signout" type="button">Sign out</button>
          </div>
        </div>
      </nav>`;
  }

  function buildColophon() {
    return `
      <footer class="colophon">
        <div class="inner">
          <div>
            <h5>The Colophon</h5>
            <p>Panakoes Admin v0.1 &middot; the Tier 1+2+3 operator console.</p>
            <p>Set in Source Serif 4 (Frank Grie&szlig;hammer), Inter Tight (Rasmus Andersson), and JetBrains Mono.</p>
            <p>Press style mocked. Tier 3 lifecycle operations are still gated by Better-Auth step-up MFA at the API layer.</p>
          </div>
          <div>
            <h5>Audit &amp; Provenance</h5>
            <p>Every Tier 3 mutation writes to <code>panakoes-dev-audit-log</code> before AND after the operation runs (ADR-032).</p>
            <p>Anomalies reconcile against the cost-cache &amp; deviation thresholds defined in ADR-031.</p>
          </div>
          <div>
            <h5>Subscription</h5>
            <p>This mockup variant: <code>warm-editorial</code> (2 of 5).</p>
            <p>Sibling variants: dense-terminal, glass-modern, brutalist-data, soft-pastel.</p>
            <p>&copy; 2026 Lafayette Labs LLC &middot; MIT licensed</p>
          </div>
        </div>
      </footer>`;
  }

  function inject() {
    const body = document.body;
    const currentKey = body.getAttribute('data-current') || '';

    const top = document.createElement('div');
    top.innerHTML = buildVariantStrip() + buildMasthead() + buildFolioNav(currentKey);
    while (top.firstChild) body.insertBefore(top.firstChild, body.firstChild);

    const foot = document.createElement('div');
    foot.innerHTML = buildColophon();
    while (foot.firstChild) body.appendChild(foot.firstChild);

    // dateline today injection
    document.querySelectorAll('[data-dateline-today]').forEach(el => {
      const d = new Date();
      const fmt = d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
      el.textContent = fmt;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
