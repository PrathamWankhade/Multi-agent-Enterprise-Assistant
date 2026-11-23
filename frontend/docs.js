/* ==========================================================================
   Documentation viewer — markdown renderer + navigation
   ========================================================================== */

const DOCS = {
  OVERVIEW: { file: 'README.md', title: 'Overview', crumb: 'Guides' },
  ARCHITECTURE: { file: 'ARCHITECTURE.md', title: 'Architecture', crumb: 'Guides' },
  AGENTS: { file: 'AGENTS.md', title: 'Agents', crumb: 'Guides' },
  API: { file: 'API.md', title: 'API Reference', crumb: 'Guides' },
  TOOLS: { file: 'TOOLS.md', title: 'Tools & Approvals', crumb: 'Guides' },
  CONFIGURATION: { file: 'CONFIGURATION.md', title: 'Configuration', crumb: 'Guides' },
  TROUBLESHOOTING: { file: 'TROUBLESHOOTING.md', title: 'Troubleshooting', crumb: 'Guides' },
  DEVELOPMENT: { file: 'DEVELOPMENT.md', title: 'Development', crumb: 'Guides' },
};

const $ = (id) => document.getElementById(id);
let mermaidReady = false;
let mermaidLib = null;

/* ------------------------------------------------------------------ markdown */

function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function renderMarkdown(src) {
  let text = String(src || '');

  // Strip GitHub-only HTML wrappers BEFORE escaping (README uses them).
  text = text.split('\n').map((line) => {
    const t = line.trim();
    const summary = t.match(/^<summary>(.*?)<\/summary>$/i);
    if (summary) return '**▸ ' + summary[1] + '**';
    if (/^<\/?(div|details|center|br|hr|p)(\s|>|$)/i.test(t)) return '';
    return line;
  }).join('\n');

  // Strip YAML-ish HTML comments used for spacing, keep the rest.
  // Protect fenced code blocks (including Mermaid) first.
  const blocks = [];
  text = text.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const key = blocks.length;
    const kind = lang === 'mermaid' ? 'mermaid' : 'code';
    blocks.push({ kind, code: code.replace(/\n$/, '') });
    return `\u0000B${key}\u0000`;
  });

  text = escapeHtml(text);
  // Handle GitHub badge links: [![alt](img)](link) -> styled pill (no external image needed)
  text = text.replace(/\[!\[([^\]]*)\]\(([^)]*)\)\]\(([^)]*)\)/g, (_, alt, img, href) =>
    (href ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' : '') +
    '<span class="md-badge">' + alt + '</span>' +
    (href ? '</a>' : '')
  );
  // Plain images ![alt](url) -> pill too
  text = text.replace(/!\[([^\]]*)\]\(([^)]*)\)/g, '<span class="md-badge">$1</span>');
  text = text.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) =>
    '<a href="' + href + '"' +
    (/^https?:/.test(href) ? ' target="_blank" rel="noopener noreferrer"' : '') +
    '>' + label + '</a>'
  );

  const lines = text.split('\n');
  const out = [];
  let listType = null, inTable = false, inQuote = false, paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push('<p>' + paragraph.join(' ') + '</p>');
      paragraph = [];
    }
  };
  const closeList = () => { if (listType) { out.push('</' + listType + '>'); listType = null; } };
  const closeTable = () => { if (inTable) { out.push('</tbody></table>'); inTable = false; } };
  const closeQuote = () => { if (inQuote) { out.push('</blockquote>'); inQuote = false; } };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();

    // protected blocks
    const blockMatch = t.match(/^\u0000B(\d+)\u0000$/);
    if (blockMatch) {
      flushParagraph(); closeList(); closeTable(); closeQuote();
      const b = blocks[Number(blockMatch[1])];
      out.push(b.kind === 'mermaid'
        ? '<div class="mermaid">' + b.code + '</div>'
        : '<pre><code>' + b.code + '</code></pre>');
      continue;
    }

    // tables
    if (/^\|.*\|$/.test(t)) {
      flushParagraph(); closeList();
      const cells = t.slice(1, -1).split('|').map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; // divider row
      if (!inTable) {
        const next = (lines[i + 1] || '').trim();
        const headerNext = /^\|[\s:|-]+\|$/.test(next);
        out.push('<table>');
        if (headerNext) {
          out.push('<thead><tr>' + cells.map((c) => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>');
          inTable = true;
          continue;
        }
        out.push('<tbody>');
        inTable = true;
      }
      out.push('<tr>' + cells.map((c) => '<td>' + c + '</td>').join('') + '</tr>');
      continue;
    }
    closeTable();

    // headings
    const h = t.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushParagraph(); closeList(); closeQuote();
      const level = Math.min(h[1].length, 4);
      out.push('<h' + level + '>' + h[2] + '</h' + level + '>');
      continue;
    }

    // horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      flushParagraph(); closeList(); closeQuote(); out.push('<hr>'); continue;
    }

    // blockquote
    if (/^>\s?/.test(t)) {
      flushParagraph(); closeList();
      if (!inQuote) { out.push('<blockquote>'); inQuote = true; }
      out.push(t.replace(/^>\s?/, '') + '<br>');
      continue;
    }
    closeQuote();

    // lists
    const bullet = t.match(/^[-*+]\s+(.*)$/);
    const numbered = t.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const want = bullet ? 'ul' : 'ol';
      if (listType !== want) { closeList(); out.push('<' + want + '>'); listType = want; }
      out.push('<li>' + (bullet ? bullet[1] : numbered[1]) + '</li>');
      continue;
    }
    closeList();

    // blank line
    if (t === '') { flushParagraph(); continue; }
    paragraph.push(line);
  }
  flushParagraph(); closeList(); closeTable(); closeQuote();
  return out.join('');
}

/* ------------------------------------------------------------------ mermaid */

async function renderMermaid() {
  const targets = document.querySelectorAll('#docs-content .mermaid');
  if (!targets.length) return;
  if (!mermaidReady) {
    try {
      mermaidLib = (await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')).default;
      const dark = document.documentElement.dataset.theme === 'dark';
      mermaidLib.initialize({
        startOnLoad: false,
        theme: dark ? 'dark' : 'neutral',
        themeVariables: { fontFamily: 'Inter, sans-serif', fontSize: '14px' },
        securityLevel: 'loose',
      });
      mermaidReady = true;
    } catch (_) {
      // Offline: leave diagrams as readable source code.
      targets.forEach((el) => { el.classList.add('offline'); });
      return;
    }
  }
  try {
    await mermaidLib.run({ nodes: targets });
  } catch (_) { /* keep source visible */ }
}

/* ------------------------------------------------------------------ nav */

async function loadPage(key) {
  const meta = DOCS[key];
  if (!meta) return;
  document.querySelectorAll('.nav-link[data-page]').forEach((a) =>
    a.classList.toggle('active', a.dataset.page === key)
  );
  $('docs-current').textContent = meta.title;
  $('docs-crumb').textContent = meta.crumb;
  const content = $('docs-content');
  content.innerHTML = '<div class="docs-loading">Loading ' + meta.title + '…</div>';
  $('docs-scroll').scrollTop = 0;

  // README is served from the project root; other docs from /docs-md/.
  const url = key === 'OVERVIEW' ? '/readme.md' : '/docs-md/' + DOCS[key].file;
  let markdown = '';
  try {
    const res = await fetch(url);
    markdown = res.ok ? await res.text() : '# Documentation unavailable\nCould not load ' + meta.title + '.';
  } catch (_) {
    markdown = '# Documentation unavailable\nCheck that the server is running.';
  }
  content.innerHTML = renderMarkdown(markdown);
  // Map GitHub-style emoji anchors (e.g. #-features) to our plain headings.
  const anchors = ['features','architecture','quick-start','documentation','api-reference','testing','project-structure','graceful-degradation','roadmap'];
  content.querySelectorAll('a[href^="#"]').forEach((a) => {
    const h = a.getAttribute('href').slice(1).replace(/^-+/, '');
    if (anchors.includes(h)) a.setAttribute('href', '#' + h);
  });
  // Give headings matching IDs so in-page links work.
  content.querySelectorAll('h2').forEach((el) => {
    const id = el.textContent.toLowerCase().replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-');
    el.id = id;
  });
  await renderMermaid();
}

/* ------------------------------------------------------------------ theme */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('docs-theme-icon').innerHTML = theme === 'dark'
    ? '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    : '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/></svg>';
  $('docs-theme-label').textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
  try { localStorage.setItem('eaa.theme', theme); } catch (_) {}
  if (mermaidReady) renderMermaid().catch(() => {});
}

/* ------------------------------------------------------------------ boot */

document.querySelectorAll('.nav-link[data-page]').forEach((a) => {
  a.addEventListener('click', (e) => {
    e.preventDefault();
    loadPage(a.dataset.page);
    closeSidebar();
  });
});
$('docs-theme-toggle').addEventListener('click', () => {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});
$('docs-menu').addEventListener('click', () => $('docs-sidebar').classList.add('open'));
function closeSidebar() { $('docs-sidebar').classList.remove('open'); }

let saved = null;
try { saved = localStorage.getItem('eaa.theme'); } catch (_) {}
applyTheme(saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

loadPage('OVERVIEW');
