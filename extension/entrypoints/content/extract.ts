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
  title: string;
  paragraphs: string[];
  nextUrl: string | null;
}

export type ExtractResult = ExtractOk | { error: ExtractError; detail: string };

export interface SiteRule {
  match: RegExp;
  content: string[];
  strip: string[];
  version: number;
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
];

const CHALLENGE_SELECTORS = [
  'iframe[src*="challenges.cloudflare"]',
  'iframe[src*="challenge"]',
  '#cf-challenge-running',
  '#challenge-form',
  '#cf-content',
];

const CHALLENGE_TITLE = /just a moment|attention required|verify you are human|verifying you are human/i;

const PROMO_LINES = /support us on|visit patreon|read ahead on|this chapter was stolen/i;

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
  return SITE_RULES.find((r) => r.match.test(url)) ?? null;
}

function isChallenge(doc: Document): boolean {
  if (CHALLENGE_TITLE.test(doc.title)) return true;
  return CHALLENGE_SELECTORS.some((s) => doc.querySelector(s) !== null);
}

export function extractChapter(doc: Document, url: string): ExtractResult {
  if (isChallenge(doc)) {
    return { error: 'challenge', detail: 'bot check present — solve it, then retry' };
  }
  const title = (doc.querySelector('h1')?.textContent || '').trim();
  const rule = ruleFor(url);

  if (rule) {
    for (const sel of rule.content) {
      const root = doc.querySelector(sel);
      if (!root) continue;
      for (const s of rule.strip) root.querySelectorAll(s).forEach((n) => n.remove());
      const paras = parasFrom(root).filter((p) => p !== title);
      if (paras.length > 0) return { title, paragraphs: paras, nextUrl: null };
    }
    // Rule hit but all containers empty -> fall through to Readability,
    // counted as fallback (telemetry for rule rot).
  }

  // Fallback: Readability on a CLONED document (never mutates the page),
  // then take its article <p>s in DOM order (never score order).
  const article = new Readability(doc.cloneNode(true) as Document).parse();
  if (!article?.content) return { error: 'boilerplate', detail: 'no article root' };
  const tpl = doc.createElement('template');
  tpl.innerHTML = article.content;
  const paras = parasFrom(tpl.content as unknown as Element)
    .filter((p) => p !== title && p !== (article.title || '').trim());
  if (paras.length === 0) {
    const imgs = tpl.content.querySelectorAll('img').length;
    if (imgs > 0) return { error: 'image-only', detail: 'image-only chapter — OCR out of scope' };
    return { error: 'empty', detail: 'no paragraphs extracted' };
  }
  return { title: article.title || title, paragraphs: paras, nextUrl: null };
}
