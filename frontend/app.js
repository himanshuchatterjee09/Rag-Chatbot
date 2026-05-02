const API = '';  // same origin; set to 'http://localhost:8000' for local dev

const chatArea   = document.getElementById('chatArea');
const messages   = document.getElementById('messages');
const welcome    = document.getElementById('welcome');
const queryInput = document.getElementById('queryInput');
const sendBtn    = document.getElementById('sendBtn');
const clearBtn   = document.getElementById('clearBtn');
const streamToggle = document.getElementById('streamToggle');
const modalOverlay = document.getElementById('modalOverlay');
const modalBody  = document.getElementById('modalBody');
const modalClose = document.getElementById('modalClose');

let history = [];
let isStreaming = false;

// ── Init ──────────────────────────────────────────────────────────────────────
(async () => {
  try {
    const r = await fetch(`${API}/api/initiatives/summary`);
    if (!r.ok) return;
    const data = await r.json();
    const t = data.totals || {};
    document.getElementById('statTotal').textContent     = t.total     ?? '–';
    document.getElementById('statActive').textContent    = t.active    ?? '–';
    document.getElementById('statCompleted').textContent = t.completed ?? '–';
    document.getElementById('statProgress').textContent  =
      t.avg_progress != null ? `${Math.round(t.avg_progress)}%` : '–';
  } catch (_) {}
})();

// ── Send on Enter (Shift+Enter = newline) ─────────────────────────────────────
queryInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
});

// Auto-resize textarea
queryInput.addEventListener('input', () => {
  queryInput.style.height = 'auto';
  queryInput.style.height = Math.min(queryInput.scrollHeight, 140) + 'px';
});

sendBtn.addEventListener('click', handleSend);
clearBtn.addEventListener('click', clearChat);
modalClose.addEventListener('click', () => { modalOverlay.hidden = true; });
modalOverlay.addEventListener('click', e => {
  if (e.target === modalOverlay) modalOverlay.hidden = true;
});

// Quick-query buttons (sidebar + suggestions)
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-query]');
  if (btn) { queryInput.value = btn.dataset.query; handleSend(); }
});

// ── Core send ─────────────────────────────────────────────────────────────────
async function handleSend() {
  const question = queryInput.value.trim();
  if (!question || isStreaming) return;

  welcome.style.display = 'none';
  appendMessage('user', question);
  history.push({ role: 'user', content: question });

  queryInput.value = '';
  queryInput.style.height = 'auto';
  sendBtn.disabled = true;
  isStreaming = true;

  const botBubble = appendMessage('assistant', '');
  const textNode  = botBubble.querySelector('.bubble-text');
  const cursor    = document.createElement('span');
  cursor.className = 'cursor';
  textNode.appendChild(cursor);

  try {
    if (streamToggle.checked) {
      await streamQuery(question, textNode, cursor, botBubble);
    } else {
      await standardQuery(question, textNode, cursor, botBubble);
    }
  } catch (err) {
    cursor.remove();
    textNode.textContent = `Error: ${err.message}`;
  }

  sendBtn.disabled = false;
  isStreaming = false;
  scrollBottom();
}

async function streamQuery(question, textNode, cursor, bubble) {
  const resp = await fetch(`${API}/api/query/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      conversation_history: history.slice(-10),
      stream: true,
    }),
  });

  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

  const reader = resp.body.getReader();
  const dec    = new TextDecoder();
  let   full   = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const lines = dec.decode(value).split('\n');
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const data = line.slice(6);
      if (data === '[DONE]') break;
      try {
        const { chunk } = JSON.parse(data);
        if (chunk) {
          full += chunk;
          cursor.remove();
          textNode.innerHTML = renderMarkdown(full);
          textNode.appendChild(cursor);
          scrollBottom();
        }
      } catch (_) {}
    }
  }

  cursor.remove();
  textNode.innerHTML = renderMarkdown(full);
  history.push({ role: 'assistant', content: full });
}

async function standardQuery(question, textNode, cursor, bubble) {
  const resp = await fetch(`${API}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      conversation_history: history.slice(-10),
    }),
  });

  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();

  cursor.remove();
  textNode.innerHTML = renderMarkdown(data.answer);
  history.push({ role: 'assistant', content: data.answer });

  // Append meta (intent badge, sources, timing)
  const meta = bubble.querySelector('.meta');
  if (data.intent) {
    const badge = document.createElement('span');
    badge.className = 'intent-badge';
    badge.textContent = data.intent;
    meta.appendChild(badge);
  }
  if (data.sources && data.sources.length) {
    const btn = document.createElement('button');
    btn.className = 'sources-btn';
    btn.textContent = `${data.sources.length} source${data.sources.length > 1 ? 's' : ''}`;
    btn.onclick = () => showSources(data.sources);
    meta.appendChild(btn);
  }
  if (data.processing_time_ms) {
    const timing = document.createElement('span');
    timing.className = 'timing';
    timing.textContent = `${data.processing_time_ms} ms`;
    meta.appendChild(timing);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;
  wrap.innerHTML = `
    <div class="avatar">${role === 'user' ? '👤' : '🤖'}</div>
    <div class="bubble">
      <div class="bubble-text">${text ? renderMarkdown(text) : ''}</div>
      <div class="meta"></div>
    </div>`;
  messages.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function scrollBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function clearChat() {
  messages.innerHTML = '';
  history = [];
  welcome.style.display = '';
}

function showSources(sources) {
  modalBody.innerHTML = sources.map(s => `
    <div class="source-item">
      <div class="source-meta">
        📁 ${s.source_table}
        ${s.metadata?.department ? ` · ${s.metadata.department}` : ''}
        ${s.metadata?.status ? ` · ${s.metadata.status}` : ''}
        · score: ${s.score.toFixed(3)}
      </div>
      <div>${escapeHtml(s.content.slice(0, 400))}${s.content.length > 400 ? '…' : ''}</div>
    </div>`).join('');
  modalOverlay.hidden = false;
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Minimal markdown: **bold**, *italic*, `code`, tables, ordered/unordered lists
function renderMarkdown(text) {
  if (!text) return '';
  let html = escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,     '<em>$1</em>')
    .replace(/`([^`]+)`/g,     '<code>$1</code>');

  // Tables
  html = html.replace(/((?:\|.+\|\n)+)/g, match => {
    const rows = match.trim().split('\n').filter(r => r.trim());
    if (rows.length < 2) return match;
    const toCell = (row, tag) =>
      row.split('|').filter((_,i,a) => i>0 && i<a.length-1)
         .map(c => `<${tag}>${c.trim()}</${tag}>`).join('');
    const head = `<tr>${toCell(rows[0], 'th')}</tr>`;
    const body = rows.slice(2).map(r => `<tr>${toCell(r, 'td')}</tr>`).join('');
    return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
  });

  // Lists
  html = html.replace(/^(\d+\. .+)(\n\d+\. .+)*/gm, m =>
    `<ol>${m.split('\n').map(l => `<li>${l.replace(/^\d+\. /,'')}</li>`).join('')}</ol>`
  );
  html = html.replace(/^([-*] .+)(\n[-*] .+)*/gm, m =>
    `<ul>${m.split('\n').map(l => `<li>${l.replace(/^[-*] /,'')}</li>`).join('')}</ul>`
  );

  // Paragraphs
  html = html.replace(/\n\n+/g, '</p><p>');
  return `<p>${html}</p>`;
}
