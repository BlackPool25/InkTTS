// Chapter extraction: challenge check -> site-rule container FIRST ->
// Readability (cloned document) fallback -> strips -> promo sweep ->
// next-page stitch (cap 5, URL discipline) -> loud gates.
// Paragraphs always emitted in DOM order. Title never enters paragraphs.
import { Readability } from '@mozilla/readability';

export type ExtractError =
  | 'empty'
  | 'boilerplate'
  | 'challenge'
  | 'image-only';

export interface ExtractOk {
  title: string; // chapter label ("Chapter 6: Seeking a New Core")
  novel: string | null; // novel title ("My Step-Daughters…"), null if unknown
  paragraphs: string[];
  nextUrl: string | null;
  prevUrl: string | null;
}

export type ExtractResult = ExtractOk | { error: ExtractError; detail: string };

export interface SiteRule {
  match: RegExp;
  content: string[];
  strip: string[];
  next?: string[]; // next-chapter selectors, first validated hit wins
  prev?: string[]; // previous-chapter selectors
  version: number;
}

/** User overrides (options → JSON): same shape, matched by `match` regex
 * source. Novel-Grabber pattern: a site redesign never needs a code update. */
let userRules: SiteRule[] = [];

export function setRuleOverrides(json: string): void {
  userRules = [];
  try {
    const arr = JSON.parse(json || '[]') as {
      match?: string; content?: string[]; strip?: string[];
      next?: string[]; prev?: string[];
    }[];
    if (!Array.isArray(arr)) return;
    for (const r of arr) {
      if (!r || typeof r.match !== 'string') continue;
      userRules.push({
        match: new RegExp(r.match, 'i'),
        content: Array.isArray(r.content) ? r.content : [],
        strip: Array.isArray(r.strip) ? r.strip : [],
        next: Array.isArray(r.next) ? r.next : undefined,
        prev: Array.isArray(r.prev) ? r.prev : undefined,
        version: 0,
      });
    }
  } catch { /* invalid JSON: ignore, options page validates on save */ }
}

export const SITE_RULES: SiteRule[] = [
  {
    // NovelBuddy first (selectors verified live at build time).
    match: /novelbuddy\.com/i,
    content: ['#chapter-content', '.chapter-content', '#content'],
    strip: ['.code-block', '.ads', 'script', 'style', 'iframe',
      '#comments', '#respond', '.comment-list', '.disqus-thread',
      '[id*=disqus]', '.wpd-*'],
    version: 1,
  },
  {
    // NovelPhoenix (verified 2026-11-15: 53 <p> in .d-chapter-content,
    // no challenge on chapter pages; rel= + data-chapter-nav for nav).
    match: /novelphoenix\.com/i,
    content: ['.d-chapter-content', '.chapter-content', '#content'],
    strip: ['script', 'style', 'iframe', '.ads', '.code-block',
      '#comments', '.comment-list', '.chapternav', 'nav', '.cmt'],
    next: ['a[rel="next"]', '[data-chapter-nav="next"]', 'a.nextchap'],
    prev: ['a[rel="prev"]', '[data-chapter-nav="prev"]', 'a.prevchap'],
    version: 2,
  },
];

const CHALLENGE_SELECTORS = [
  'iframe[src*="challenges.cloudflare"]',
  'iframe[src*="challenge"]',
  'iframe[src*="turnstile"]',
  '#cf-challenge-running',
  '#challenge-form',
  '#cf-content',
  '#turnstile-wrapper',
  '.cf-turnstile',
];

const CHALLENGE_TITLE = /just a moment|attention required|verify you are human|verifying you are human|checking your browser/i;

const PROMO_SELECTORS = [
  '.translator-note', '.tn', '.author-note', '.patreon', '.promo',
  '.donation', '.support-us', '[class*=promo]', '[id*=promo]',
];

const PROMO_LINES = /support us on|visit patreon|read ahead on|this chapter was stolen|visit novelfire|go to lightnovel|join our discord|if you find this novel on/i;

export let RULE_VERSION = 1;
export let ruleFallbacks = 0; // telemetry: rule hit but empty -> fallback

function stripBlocks(root: Element): void {
  for (const s of PROMO_SELECTORS) {
    try {
      root.querySelectorAll(s).forEach((n) => n.remove());
    } catch { /* invalid selector on old pages: ignore */ }
  }
  // Block-level promo <style> rules + translator/author-note quarantine:
  // decompose whole blocks, never inline-substring (protects dialogue).
  root.querySelectorAll('p, div').forEach((el) => {
    if (PROMO_LINES.test((el.textContent || '').slice(0, 300)) && (el.textContent || '').length < 300) {
      el.remove();
    }
  });
}

const READABILITY_OPTS = {
  charThreshold: 200,
  nbTopCandidates: 10,
  classesToPreserve: ['page', 'ruby', 'rt', 'rp', 'p-novel', 'content-inner'],
} as unknown as Record<string, unknown>;

function textOf(el: Element): string {
  return (el.textContent || '').replace(/\s+/g, ' ').trim();
}

function parasFrom(root: Element): string[] {
  const out: string[] = [];
  root.querySelectorAll('p').forEach((p) => {
    const t = textOf(p);
    if (t.length >= 20 && !PROMO_LINES.test(t)) out.push(t);
  });
  return out;
}

export function ruleFor(url: string): SiteRule | null {
  return userRules.find((r) => r.match.test(url))
    ?? SITE_RULES.find((r) => r.match.test(url))
    ?? null;
}

function isChallenge(doc: Document): boolean {
  if (CHALLENGE_TITLE.test(doc.title)) return true;
  return CHALLENGE_SELECTORS.some((s) => doc.querySelector(s) !== null);
}

export function extractChapter(doc: Document, url: string): ExtractResult {
  if (isChallenge(doc)) {
    return { error: 'challenge', detail: 'bot check present — solve it, then retry' };
  }
  const { title, novel } = extractTitle(doc);
  const rule = ruleFor(url);

  if (rule) {
    for (const sel of rule.content) {
      const root = doc.querySelector(sel);
      if (!root) continue;
      const clone = root.cloneNode(true) as Element;
      for (const s of rule.strip) {
        try { clone.querySelectorAll(s).forEach((n) => n.remove()); } catch { /* bad selector: skip */ }
      }
      stripBlocks(clone);
      const paras = parasFrom(clone).filter((p) => p !== title);
      if (paras.length > 0) {
        return {
          title: title || 'Untitled chapter', novel, paragraphs: paras,
          nextUrl: findChapterUrl(doc, url, 'next'),
          prevUrl: findChapterUrl(doc, url, 'prev'),
        };
      }
    }
    // Rule hit but all containers empty -> fall through to Readability,
    // counted as fallback (telemetry for rule rot).
    ruleFallbacks += 1;
  }

  // Fallback: Readability on a CLONED document (never mutates the page),
  // then take its article <p>s in DOM order (never score order).
  const article = new Readability(doc.cloneNode(true) as Document, READABILITY_OPTS).parse();
  if (!article?.content) return { error: 'boilerplate', detail: 'no article root' };
  const tpl = doc.createElement('template');
  tpl.innerHTML = article.content;
  stripBlocks(tpl.content as unknown as Element);
  const paras = parasFrom(tpl.content as unknown as Element)
    .filter((p) => p !== title && p !== (article.title || '').trim());
  if (paras.length === 0) {
    const imgs = tpl.content.querySelectorAll('img').length;
    if (imgs > 0) return { error: 'image-only', detail: 'image-only chapter — OCR out of scope' };
    return { error: 'empty', detail: 'no paragraphs extracted' };
  }
  return {
    title: article.title || title || 'Untitled chapter', novel, paragraphs: paras,
    nextUrl: findChapterUrl(doc, url, 'next'),
    prevUrl: findChapterUrl(doc, url, 'prev'),
  };
}

/** Chapter title + novel, layered: og:title minus site_name, then
 * chapter-dropdown selection, then h1, then document.title. */
export function extractTitle(doc: Document): { title: string; novel: string | null } {
  const site = (doc.querySelector('meta[property="og:site_name"]')?.getAttribute('content') || '').trim();
  const og = (doc.querySelector('meta[property="og:title"]')?.getAttribute('content') || '').trim();
  const splitSite = (t: string): string => {
    if (site && t.endsWith(` - ${site}`)) return t.slice(0, -(` - ${site}`).length).trim();
    if (site && t.endsWith(` | ${site}`)) return t.slice(0, -(` | ${site}`).length).trim();
    return t;
  };
  const splitParts = (t: string): { title: string; novel: string | null } => {
    const parts = splitSite(t).split(/\s+-\s+/);
    if (parts.length >= 2) {
      // Convention: "Chapter 6: … - Novel Name" (chapter first).
      return { title: parts[0].trim(), novel: parts.slice(1).join(' - ').trim() || null };
    }
    return { title: t, novel: site || null };
  };
  if (og) {
    const r = splitParts(og);
    if (r.title) return r;
  }
  const selected = [...doc.querySelectorAll('select.chapindex option, select.chapter-select option')]
    .find((o) => (o as HTMLOptionElement).selected)?.textContent?.trim();
  const h1 = (doc.querySelector('h1')?.textContent || '').trim();
  if (selected && h1 && h1.includes(selected)) {
    const r = splitParts(h1);
    if (r.title) return r;
  }
  if (h1) {
    const r = splitParts(h1);
    if (r.title) return r;
  }
  if (doc.title) return splitParts(doc.title.trim());
  return { title: '', novel: site || null };
}

function anchorHref(el: Element, base: URL): string | null {
  const raw = (el as HTMLAnchorElement).getAttribute('href');
  if (!raw) return null;
  try {
    return new URL(raw, base).toString();
  } catch {
    return null;
  }
}

/** Same-host, not-self, not-TOC validation shared by every nav layer. */
function validNav(href: string, base: URL): string | null {
  let u: URL;
  try {
    u = new URL(href, base);
  } catch {
    return null;
  }
  if (u.host !== base.host) return null;
  const norm = (x: URL) => x.toString().replace(/\/$/, '');
  if (norm(u) === norm(base)) return null; // self link
  if (u.hash && norm(u) === norm(base)) return null;
  if (/\/(toc|table-of-contents|contents|chapters\/?)$/i.test(u.pathname)) return null;
  const baseRoot = base.pathname.replace(/\/[^/]+\/?$/, '/');
  if (!u.pathname.startsWith(baseRoot) && u.pathname === baseRoot.replace(/\/$/, '')) return null; // novel root
  return u.toString();
}

const NEXT_PAT = /^(next|next chapter|continue( reading)?|»|→|>)$/i;
const NEXT_HINT = /next|continue|»|→/;
const PREV_PAT = /^(prev|previous|previous chapter|last chapter|«|←|<)$/i;
const PREV_HINT = /prev|previous|back|«|←/;
const BAD_NAV = /login|sign ?in|subscribe|donate|report|comment|share|bookmark|toc|index|contents/i;
const FIRST_LAST = /^(first|last)$|^first chapter$|^last chapter$/i;

/** Chapter prev/next link, layered (highest signal first):
 * site-rule selectors → rel= → data-chapter-nav / ids / classes →
 * scored text scan (Readability-style, ≥50) → validated-or-nothing. */
export function findChapterUrl(doc: Document, url: string, dir: 'next' | 'prev'): string | null {
  let base: URL;
  try {
    base = new URL(url);
  } catch {
    return null;
  }
  const rule = ruleFor(url);
  const sels = dir === 'next' ? rule?.next : rule?.prev;
  if (sels) {
    for (const sel of sels) {
      try {
        const el = doc.querySelector(sel) as HTMLAnchorElement | null;
        const href = el ? anchorHref(el, base) : null;
        if (href) {
          const v = validNav(href, base);
          if (v) return v;
        }
      } catch { /* bad selector: try next layer */ }
    }
  }
  const rel = doc.querySelector(`a[rel="${dir}"]`) as HTMLAnchorElement | null;
  const relHref = rel ? anchorHref(rel, base) : null;
  if (relHref) {
    const v = validNav(relHref, base);
    if (v) return v;
  }
  const hooked = doc.querySelector(`[data-chapter-nav="${dir}"]`) as HTMLAnchorElement | null;
  const hookHref = hooked ? anchorHref(hooked, base) : null;
  if (hookHref) {
    const v = validNav(hookHref, base);
    if (v) return v;
  }
  for (const sel of dir === 'next'
    ? ['#next', 'a#next', '.nextchap', '.next', '.next-btn', 'a.next-link']
    : ['#prev', 'a#prev', '.prevchap', '.prev', '.prev-btn', 'a.prev-link']) {
    try {
      const el = doc.querySelector(sel) as HTMLAnchorElement | null;
      const href = el ? anchorHref(el, base) : null;
      if (href) {
        const v = validNav(href, base);
        if (v) return v;
      }
    } catch { /* next selector */ }
  }
  // Scored scan: every same-host link, Readability-style weights.
  const wantExact = dir === 'next' ? NEXT_PAT : PREV_PAT;
  const wantStart = dir === 'next'
    ? /^next(\s|chapter|»|→|>|\\||$)/i
    : /^(prev|previous)(\s|chapter|«|←|<|\\||$)/i;
  const wantHint = dir === 'next' ? NEXT_HINT : PREV_HINT;
  const avoidHint = dir === 'next' ? PREV_HINT : NEXT_HINT;
  let best: string | null = null;
  let bestScore = 49; // require ≥50
  const pathPref = base.pathname.split('/').slice(0, 4).join('/');
  doc.querySelectorAll('a[href]').forEach((node) => {
    const el = node as HTMLAnchorElement;
    const text = (el.textContent || '').trim();
    if (!text || text.length > 25 || BAD_NAV.test(text)) return;
    const sig = `${text} ${el.className ?? ''} ${el.id ?? ''}`.toLowerCase();
    let score = 0;
    if (wantExact.test(text) || wantStart.test(text)) score += 50;
    else if (wantHint.test(sig)) score += 25;
    if (avoidHint.test(text)) score -= 200;
    if (FIRST_LAST.test(text)) score -= 65;
    if (sig.includes('chapter') || sig.includes('chap')) score += 10;
    if (score < 50) return;
    const href = anchorHref(el, base);
    if (!href) return;
    try {
      if (new URL(href).pathname.startsWith(pathPref)) score += 30;
    } catch { /* ignore */ }
    if (score > bestScore) {
      const v = validNav(href, base);
      if (v) {
        best = v;
        bestScore = score;
      }
    }
  });
  // Chapter-dropdown options with URL values: neighbors of selected.
  if (!best) {
    const opts = [...doc.querySelectorAll('select.chapindex option, select.chapter-select option')] as HTMLOptionElement[];
    const values = opts.map((o) => o.value).filter((v) => /^https?:/i.test(v));
    if (values.length > 1) {
      const cur = normUrl(url);
      const i = values.findIndex((v) => normUrl(v) === cur);
      const pick = dir === 'next' ? values[i + 1] : values[i - 1];
      if (i >= 0 && pick) {
        const v = validNav(pick, base);
        if (v) return v;
      }
    }
  }
  return best;
}

function normUrl(u: string): string {
  try {
    return new URL(u).toString().replace(/\/$/, '');
  } catch {
    return u;
  }
}

/** Settle-wait: chapter paragraphs loaded lazily — wait until the child
 * count is stable 2x500ms (3s cap), then extract. */
export async function settleAndExtract(doc: Document, url: string): Promise<ExtractResult> {
  const root = doc.querySelector('#chapter-content, .chapter-content, article') ?? doc.body;
  let last = -1;
  let stable = 0;
  for (let i = 0; i < 6; i++) {
    const n = root.childElementCount;
    if (n === last) {
      stable += 1;
      if (stable >= 2) break;
    } else {
      stable = 0;
      last = n;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return extractChapter(doc, url);
}
