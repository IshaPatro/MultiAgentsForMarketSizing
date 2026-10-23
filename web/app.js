(() => {
  'use strict';
  const tag = document.getElementById('report-data');
  if (!tag || document.body.dataset.view !== 'biobot') return;
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
  const names = {VC:'Venture Capitalist',CEO:'Chief Executive',CFO:'Chief Financial Officer',CTO:'Chief Technology Officer',DEVIL:'Devil’s Advocate',MODERATOR:'Moderator','VC ASSISTANT':'Fact-checker'};
  const portraits = {VC:'vc',CEO:'ceo',CFO:'cfo',CTO:'cto',DEVIL:'devil',MODERATOR:'moderator','VC ASSISTANT':'assistant'};
  const avatar = speaker => `<span class="agent-avatar" data-role="${esc(speaker)}">${portraits[speaker] ? `<img src="avatars/${portraits[speaker]}.svg" alt="" width="40" height="40">` : esc(speaker.slice(0, 1))}</span>`;
  let activeProfile = location.hash === '#naive' || location.hash === '#opus_low' ? 'opus_low' : 'routed';
  const activeRun = () => activeProfile === 'routed' ? optimized : naive;

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
    const display = speaker === 'DEVIL' ? 'Devil’s Advocate' : speaker === 'VC ASSISTANT' ? 'VC Assistant' : speaker;
    return `<article class="boardroom-turn" data-role="${esc(speaker)}">${avatar(speaker)}<div class="turn-body"><div class="turn-header"><strong>${esc(display)}</strong><span>${esc(names[speaker] || speaker)}</span><span>→ ${esc(turn.addressed_to || 'room')}</span><small>#${number(turn.seq)}</small></div>${turn.topic ? `<div class="turn-topic">${esc(topics[turn.topic] || turn.topic)}${turn.follow_up ? ' · follow-up' : ''}</div>` : ''}<div class="turn-bubble">${esc(turn.content)}</div>${support.length ? `<details class="turn-support"><summary>Math, assumptions & fact checks (${support.length})</summary><ul>${support.join('')}</ul></details>` : ''}</div></article>`;
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
      const divider = turn.round !== previousRound ? `<div class="round-divider">Round ${esc(turn.round)}</div>` : '';
      previousRound = turn.round;
      return divider + turnHtml(turn);
    }).join('') || '<div class="empty">No messages match this search.</div>';
    return `${transcript}<div class="chat-footer">${number(filtered.length)} of ${number(run.turns?.length)} messages shown · ${esc(label(run.profile))}</div>`;
  }

  function renderConversations() {
    const search = document.getElementById('conversation-search');
    const filter = document.getElementById('role-filter');
    const render = () => {
      const run = activeRun();
      const query = search.value.trim().toLowerCase();
      document.getElementById('conversation').innerHTML = conversation(run, query, filter.value);
      document.getElementById('selected-memo').innerHTML = memo(run);
      document.getElementById('summary-decision').textContent = run ? decisionName(run.decision?.decision) : '—';
      document.getElementById('summary-conviction').textContent = run?.decision?.conviction == null ? '—' : Math.round(Number(run.decision.conviction) * 100) + '%';
      document.getElementById('summary-turns').textContent = run ? number(run.turns?.length) + ' messages' : '—';
      document.getElementById('summary-cost').textContent = run ? money(run.cost) : '—';
      document.getElementById('conversation-subtitle').textContent = run ? `${number(run.turns?.length)} messages · ${new Set((run.turns || []).map(turn => turn.speaker)).size} voices · ${label(run.profile)}` : 'No saved run available.';
      document.querySelectorAll('input[name="approach"]').forEach(input => { input.checked = input.value === activeProfile; });
    };
    document.querySelectorAll('input[name="approach"]').forEach(input => input.addEventListener('change', () => {
      activeProfile = input.value;
      history.replaceState(null, '', '#' + (activeProfile === 'routed' ? 'optimized' : 'naive'));
      render();
    }));
    window.addEventListener('hashchange', () => { activeProfile = location.hash === '#naive' || location.hash === '#opus_low' ? 'opus_low' : 'routed'; render(); });
    search.addEventListener('input', render);
    filter.addEventListener('change', render);
    render();
  }

  function renderSidebar() {
    const speakers = [...new Set(runs.flatMap(run => (run.turns || []).map(turn => role(turn.speaker))))];
    const order = ['VC','CEO','CFO','CTO','DEVIL','MODERATOR','VC ASSISTANT'];
    document.getElementById('agent-roster').innerHTML = order.filter(name => speakers.includes(name)).map(name => `<div class="roster-row">${avatar(name)}<span><strong>${esc(name === 'DEVIL' ? 'Devil’s Advocate' : name === 'VC ASSISTANT' ? 'VC Assistant' : name)}</strong><small>${esc(names[name])}</small></span></div>`).join('');
    const tokens = run => run ? (run.metrics?.input_tokens || 0) + (run.metrics?.output_tokens || 0) : 0;
    const rows = [
      ['Cost', optimized ? money(optimized.cost) : '—', naive ? money(naive.cost) : '—'],
      ['Tokens', optimized ? number(tokens(optimized)) : '—', naive ? number(tokens(naive)) : '—'],
      ['Runtime', optimized?.metrics?.wall_s == null ? '—' : number(optimized.metrics.wall_s) + 's', naive?.metrics?.wall_s == null ? '—' : number(naive.metrics.wall_s) + 's'],
      ['Calls', optimized ? number(optimized.metrics?.calls) : '—', naive ? number(naive.metrics?.calls) : '—'],
    ];
    document.getElementById('profile-comparison').innerHTML = rows.map(([name, a, b]) => `<tr><th scope="row">${name}</th><td>${a}</td><td>${b}</td></tr>`).join('');
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
    const conviction = d.conviction == null ? '—' : Math.round(Number(d.conviction) * 100) + '%';
    return `<div class="decision-card"><div class="eyebrow">FINAL RECOMMENDATION</div><div class="decision-main"><strong>${esc(decisionName(d.decision))}</strong><span class="conviction-ring" style="--progress:${esc(conviction)}"><span>${esc(conviction)}</span></span></div><p>${esc(d.deal_terms_sketch || 'No recommendation text recorded.')}</p></div><section class="memo-section"><h3>Investment rubric</h3><div class="score-list">${scores || '<p>No rubric scores recorded.</p>'}</div></section>${list('Reasons in favor', d.key_reasons_for)}${list('Reasons against', d.key_reasons_against)}${list('What would change the decision', d.what_would_change_my_mind)}<p class="memo-note">${run.illustrative ? 'Illustrative public memo.' : 'Memo from the selected saved run.'}</p>`;
  }

  renderSidebar();
  renderConversations();
})();
