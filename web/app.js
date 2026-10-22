(() => {
  'use strict';
  const tag = document.getElementById('report-data');
  if (!tag) return;
  const data = JSON.parse(tag.textContent);
  const runs = data.runs || [];
  const optimized = runs.find(run => run.company === 'biobot' && run.profile === 'routed');
  const naive = runs.find(run => run.company === 'biobot' && run.profile === 'opus_low');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const number = value => Number(value || 0).toLocaleString('en-US');
  const money = value => '$' + Number(value || 0).toFixed(4);
  const label = profile => profile === 'routed' ? 'Optimized · JEV + Claude' : 'Naive · Opus-only';
  const role = speaker => speaker === 'ASSISTANT' ? 'VC ASSISTANT' : speaker;
  const decisionName = value => ({pass:'Pass', invest:'Invest', invest_with_conditions:'Invest with conditions', defer:'Defer'})[value] || String(value || 'Pending').replaceAll('_', ' ');
  const topics = {market_size:'Market size',customers:'Customers',unit_economics:'Unit economics',technology_moat:'Technology & moat',go_to_market:'Go-to-market',risks:'Risks'};

  function metrics(run) {
    const m = run.metrics || {};
    return `<div class="run-metrics"><div><span>Cost</span><b>${money(run.cost)}</b></div><div><span>Tokens</span><b>${number((m.input_tokens || 0) + (m.output_tokens || 0))}</b></div><div><span>Runtime</span><b>${m.wall_s == null ? '—' : number(m.wall_s) + 's'}</b></div></div>`;
  }

  function turnHtml(turn) {
    const speaker = role(turn.speaker);
    const claims = (turn.claims || []).filter(claim => claim.verdict !== 'not_checked');
    const support = [];
    if (turn.calculation) support.push(`<li><b>Calculation${turn.calculation_ok === 1 ? ' · checked' : turn.calculation_ok === 0 ? ' · incorrect' : ''}:</b> ${esc(turn.calculation)}</li>`);
    for (const item of turn.assumptions || []) support.push(`<li><b>Assumption:</b> ${esc(item)}</li>`);
    for (const claim of claims) support.push(`<li><span class="verdict ${esc(claim.verdict)}">${esc(claim.verdict.replaceAll('_', ' '))}</span> · ${esc(claim.text)}${claim.note ? ` — ${esc(claim.note)}` : ''}</li>`);
    for (const id of turn.evidence_ids || []) support.push(`<li><b>Retrieved evidence:</b> ${esc(id)}</li>`);
    return `<article class="turn" data-role="${esc(speaker)}"><div class="turn-header"><span class="role-badge">${esc(speaker === 'DEVIL' ? "DEVIL'S ADVOCATE" : speaker)}</span><span class="to">→ ${esc(turn.addressed_to || 'room')}</span><span class="seq">#${number(turn.seq)}</span></div>${turn.topic ? `<div class="topic">${esc(topics[turn.topic] || turn.topic)}${turn.follow_up ? ' · follow-up' : ''}</div>` : ''}<p>${esc(turn.content)}</p>${support.length ? `<details><summary>Math, assumptions & fact checks (${support.length})</summary><ul>${support.join('')}</ul></details>` : ''}</article>`;
  }

  function conversation(run, query, selectedRole) {
    if (!run) return '<div class="empty">No saved run is available for this profile.</div>';
    const filtered = (run.turns || []).filter(turn => {
      const matchesRole = selectedRole === 'all' || role(turn.speaker) === selectedRole;
      const matchesQuery = !query || JSON.stringify(turn).toLowerCase().includes(query);
      return matchesRole && matchesQuery;
    });
    let previousRound = null;
    const transcript = filtered.map(turn => {
      const divider = turn.round !== previousRound ? `<div class="round-title">Round ${esc(turn.round)}</div>` : '';
      previousRound = turn.round;
      return divider + turnHtml(turn);
    }).join('') || '<div class="empty">No messages match this search.</div>';
    return `<div class="run-heading"><span class="tag">${esc(run.profile === 'routed' ? 'OPTIMIZED' : 'NAIVE BASELINE')}</span><h2>${esc(label(run.profile))}</h2><p>${number((run.turns || []).length)} messages · ${number((run.metrics || {}).calls)} model calls${run.illustrative ? ' · illustrative dialogue' : ''}</p>${metrics(run)}</div><div class="run-turns">${transcript}</div>`;
  }

  function renderConversations() {
    const search = document.getElementById('conversation-search');
    const filter = document.getElementById('role-filter');
    const render = () => {
      const query = search.value.trim().toLowerCase();
      document.getElementById('optimized-run').innerHTML = conversation(optimized, query, filter.value);
      document.getElementById('naive-run').innerHTML = conversation(naive, query, filter.value);
    };
    search.addEventListener('input', render);
    filter.addEventListener('change', render);
    render();
  }

  function list(title, items) {
    if (!items || !items.length) return '';
    return `<section class="memo-section"><h3>${esc(title)}</h3><ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul></section>`;
  }

  function memo(run) {
    if (!run) return '<div class="empty">No saved VC decision is available for this profile.</div>';
    const d = run.decision || {};
    const scores = Object.entries(d.scores || {}).map(([name, score]) => {
      const reason = d.score_drivers?.[name]?.reason || '';
      const title = name.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
      return `<div class="score-row" title="${esc(reason)}"><span>${esc(title)}</span><span class="score-track"><i style="width:${Math.max(0, Math.min(100, Number(score) * 20))}%"></i></span><b>${esc(score)}/5</b></div>`;
    }).join('');
    return `<div class="memo-header"><div><span class="tag">${esc(run.profile === 'routed' ? 'OPTIMIZED' : 'NAIVE BASELINE')}</span><h2>${esc(label(run.profile))}</h2><p>${esc(run.id)}${run.illustrative ? ' · illustrative memo' : ''}</p></div><span class="decision-pill">${esc(decisionName(d.decision))}</span></div>${metrics(run)}<section class="memo-section"><div class="memo-summary"><strong>VC recommendation · ${d.conviction == null ? 'conviction unavailable' : Math.round(Number(d.conviction) * 100) + '% conviction'}</strong><p>${esc(d.deal_terms_sketch || 'No recommendation text recorded.')}</p></div></section><section class="memo-section"><h3>Investment rubric</h3><div class="score-list">${scores || '<p>No rubric scores recorded.</p>'}</div></section>${list('Reasons in favor', d.key_reasons_for)}${list('Reasons against', d.key_reasons_against)}${list('What would change the decision', d.what_would_change_my_mind)}${run.report_available ? `<section class="memo-section"><a class="button secondary" href="../reports/runs/${encodeURIComponent(run.id)}.md">Open local run report →</a></section>` : ''}`;
  }

  if (document.body.dataset.view === 'conversations') renderConversations();
  if (document.body.dataset.view === 'memos') {
    document.getElementById('optimized-memo').innerHTML = memo(optimized);
    document.getElementById('naive-memo').innerHTML = memo(naive);
  }
})();
