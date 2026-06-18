(function () {
  function tab(name) {
    const pane = document.getElementById('tab-' + name);
    if (!pane) return;
    document.querySelectorAll('.tab-pane').forEach((element) => element.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach((element) => element.classList.remove('active'));
    pane.classList.remove('hidden');
    const button = document.querySelector(`.tab-btn[data-tab="${name}"]`);
    if (button) button.classList.add('active');
    window.location.hash = 'pl-' + name;
    if (name === 'auto' && typeof window.loadAutoRules === 'function') {
      window.loadAutoRules();
    }
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    return response.json();
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function renderMetricCards(items) {
    return `<div class="result-metrics">${items.map((item) => `
      <article class="metric-card ${item.tone || ''}">
        <strong>${escapeHtml(item.value)}</strong>
        <span>${escapeHtml(item.label)}</span>
      </article>`).join('')}</div>`;
  }

  function renderTagList(items, emptyText) {
    if (!items || !items.length) return `<p class="muted">${escapeHtml(emptyText)}</p>`;
    return `<div class="tag-list">${items.map((item) => `<span class="tag-chip">${escapeHtml(item)}</span>`).join('')}</div>`;
  }

  function renderResultPanel(title, body, meta = '') {
    return `<section class="surface result-panel">
      <div class="section-head">
        <h2>${escapeHtml(title)}</h2>
        <span class="section-meta">${escapeHtml(meta)}</span>
      </div>
      ${body}
    </section>`;
  }

  function renderError(container, message) {
    container.innerHTML = `<section class="surface result-panel"><p class="err">❌ ${escapeHtml(message)}</p></section>`;
  }

  window.tab = tab;
  window.post = post;
  window.escapeHtml = escapeHtml;
  window.renderMetricCards = renderMetricCards;
  window.renderTagList = renderTagList;
  window.renderResultPanel = renderResultPanel;
  window.renderError = renderError;
})();