import { describe, expect, it, vi } from 'vitest';
import { extractAuthor, extractChapter, extractTitle, findChapterUrl, findPageNext, setRuleOverrides, stitchChapterPages } from '../entrypoints/content/extract';
import type { ExtractOk } from '../entrypoints/content/extract';

function doc(html: string, url = 'https://novelbuddy.com/ch/1'): Document {
  const d = document.implementation.createHTMLDocument('t');
  d.open();
  d.write(html);
  d.close();
  Object.defineProperty(d, 'URL', { value: url });
  return d;
}

const NOVEL = `<html><head><title>Ch 1 - Sunny Day</title></head><body>
<h1>Chapter 1: Sunny Day</h1><nav>Home Library</nav>
<div id="chapter-content">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets.</p>
<p>"You should read more books," she said, and left the library quietly without another word.</p>
<p>He walked home slowly through the rain, thinking of nothing at all in particular.</p>
</div><div id="comments"><div class="comment-list"><p>Nice chapter!!!</p></div></div>
</body></html>`;

const PROMO = `<html><head><title>Ch 2</title></head><body><h1>Chapter 2</h1>
<div id="chapter-content">
<p>The road stretched ahead through the misty valley toward the distant and gloomy mountains.</p>
<p>Support us on Patreon to read ahead on many more exciting chapters every single week!</p>
<p>She drew her sword and faced the darkness gathering at the edge of the forest.</p>
</div></body></html>`;

const CHALLENGE = `<html><head><title>Just a moment...</title></head><body>
<h1>Verifying you are human</h1>
<iframe src="https://challenges.cloudflare.com/turnstile/v0/x"></iframe>
<div id="chapter-content"><p>Short teaser paragraph here for testing purposes only right now.</p></div>
</body></html>`;

const IMAGE = `<html><head><title>Ch 3</title></head><body><h1>Chapter 3</h1>
<article><img src="page1.jpg" /></article></body></html>`;

describe('extractChapter', () => {
  it('extracts clean paragraphs, drops comments, title not first', () => {
    const r = extractChapter(doc(NOVEL), 'https://novelbuddy.com/ch/1');
    if (!('paragraphs' in r)) throw new Error('expected ok: ' + JSON.stringify(r));
    expect(r.paragraphs.length).toBe(3);
    expect(r.paragraphs[0]).not.toBe(r.title);
    expect(r.paragraphs.join(' ')).not.toMatch(/Nice chapter/);
  });

  it('drops promo paragraphs inside container', () => {
    const r = extractChapter(doc(PROMO), 'https://novelbuddy.com/ch/2');
    if (!('paragraphs' in r)) throw new Error('expected ok');
    expect(r.paragraphs.length).toBe(2);
    expect(r.paragraphs.join(' ')).not.toMatch(/Patreon/);
  });

  it('dialogue with read more survives (block-level strip only)', () => {
    const r = extractChapter(doc(NOVEL), 'https://novelbuddy.com/ch/1');
    if (!('paragraphs' in r)) throw new Error('expected ok');
    expect(r.paragraphs.join(' ')).toMatch(/read more books/);
  });

  it('Turnstile challenge fails loud, not narrated', () => {
    const r = extractChapter(doc(CHALLENGE), 'https://novelbuddy.com/ch/9');
    expect(r).toMatchObject({ error: 'challenge' });
  });

  it('image-only fails loud', () => {
    const r = extractChapter(doc(IMAGE), 'https://unknown-site.example/ch/3');
    // rule path misses (unknown site) -> Readability fallback on article
    expect(r).toMatchObject({ error: 'image-only' });
  });
});

const PHOENIX = `<html><head>
<meta property="og:site_name" content="Novel Phoenix" />
<meta property="og:title" content="Chapter 6 - My Step-Daughters Are The Villainesses - Novel Phoenix" />
<title>Chapter 6 - Novel Phoenix</title></head><body>
<h1>My Step-Daughters Are The Villainesses - Chapter 6: Seeking a New Core</h1>
<a rel="prev" href="/novel/my-step/chapter-5">Prev</a>
<a rel="next" class="nextchap" href="/novel/my-step/chapter-7">Next</a>
<div class="d-chapter-content">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets on the docks.</p>
<p>"You should read more books," she said, and left the library quietly without another word to anyone present.</p>
</div></body></html>`;

describe('title + chapter nav', () => {
  it('og:title splits chapter from novel', () => {
    const d = doc(PHOENIX, 'https://novelphoenix.com/novel/my-step/chapter-6');
    const t = extractTitle(d);
    expect(t.title).toMatch(/Chapter 6/);
    expect(t.novel).toMatch(/Villainesses/);
  });

  it('rel=next/prev win for chapter nav', () => {
    const d = doc(PHOENIX, 'https://novelphoenix.com/novel/my-step/chapter-6');
    expect(findChapterUrl(d, 'https://novelphoenix.com/novel/my-step/chapter-6', 'next'))
      .toContain('/chapter-7');
    expect(findChapterUrl(d, 'https://novelphoenix.com/novel/my-step/chapter-6', 'prev'))
      .toContain('/chapter-5');
  });

  it('extractChapter carries title/novel/prev/next', () => {
    const r = extractChapter(doc(PHOENIX, 'https://novelphoenix.com/novel/my-step/chapter-6'),
      'https://novelphoenix.com/novel/my-step/chapter-6');
    if (!('paragraphs' in r)) throw new Error('expected ok: ' + JSON.stringify(r));
    expect(r.title).toMatch(/Chapter 6/);
    expect(r.novel).toMatch(/Villainesses/);
    expect(r.nextUrl).toContain('/chapter-7');
    expect(r.prevUrl).toContain('/chapter-5');
  });

  it('scored fallback finds a Next link, rejects self links', () => {
    const h = `<html><head><title>Ch 9</title></head><body><h1>Chapter 9</h1>
      <div class="content"><p>${'x '.repeat(60)}</p></div>
      <a href="/novel/x/chapter-9">Chapter 9</a>
      <a href="/novel/x/chapter-10">Next Chapter &raquo;</a></body></html>`;
    const d = doc(h, 'https://example.com/novel/x/chapter-9');
    expect(findChapterUrl(d, 'https://example.com/novel/x/chapter-9', 'next'))
      .toContain('/chapter-10');
  });

  it('user rule overrides win over generic scan', () => {
    setRuleOverrides(JSON.stringify([{ match: 'example\\.com', content: ['.content'], strip: [], next: ['a.mine'] }]));
    try {
      const h = `<html><head><title>Ch 1</title></head><body><h1>Chapter 1</h1>
        <div class="content"><p>${'y '.repeat(60)}</p></div>
        <a class="mine" href="/novel/x/chapter-2">Second</a>
        <a href="/novel/x/chapter-9">Next Chapter</a></body></html>`;
      const d = doc(h, 'https://example.com/novel/x/chapter-1');
      expect(findChapterUrl(d, 'https://example.com/novel/x/chapter-1', 'next'))
        .toContain('/chapter-2');
    } finally {
      setRuleOverrides('[]');
    }
  });
});

const LOGIN_WALL = `<html><head><title>Login</title></head><body>
<h1>Please log in to continue reading this chapter right now</h1>
<form><input type="text" name="u" /><input type="password" name="p" /></form>
</body></html>`;

const LOGIN_SIDEBAR = `<html><head><title>Ch 1</title></head><body>
<h1>Chapter 1</h1>
<aside><form><input type="password" name="p" /></form></aside>
<div id="chapter-content">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets.</p>
<p>He walked home slowly through the rain, thinking of nothing at all in particular today.</p>
</div></body></html>`;

const MADARA = `<html><head><title>Ch 5 - Some Novel</title>
<meta name="author" content="Jane Doe" /></head><body class="madara">
<h1>Chapter 5</h1>
<div class="reading-content">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets.</p>
<p>He walked home slowly through the rain, thinking of nothing at all in particular today.</p>
</div></body></html>`;

const RULE_ROT = `<html><head><title>Ch 1</title></head><body>
<h1>Chapter 1</h1>
<article><div class="redesigned-body">
<p>The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets.</p>
<p>"You should read more books," she said, and left the library quietly without another word.</p>
<p>He walked slowly through the dark marketplace, thinking of nothing at all in particular.</p>
<p>Additional filler prose to clear the readability length threshold for extraction testing.</p>
</div></article></body></html>`;

function pageHtml(title: string, body: string, nextHref: string | null): string {
  return `<html><head><title>${title}</title></head><body><h1>${title}</h1>
<div id="chapter-content">${body}</div>
${nextHref ? `<a href="${nextHref}">Next Page</a>` : ''}
</body></html>`;
}

const P = (t: string): string => `<p>${t}</p>`;
const LONG = 'The lanterns swayed in the evening wind over the quiet harbor town where old sailors mended their nets';

describe('login, author, CMS templates, rule rot', () => {
  it('login wall fails loud as locked', () => {
    const r = extractChapter(doc(LOGIN_WALL, 'https://example.com/novel/x/chapter-9'), 'https://example.com/novel/x/chapter-9');
    expect(r).toMatchObject({ error: 'locked' });
  });

  it('sidebar login form does not block a real chapter', () => {
    const r = extractChapter(doc(LOGIN_SIDEBAR, 'https://novelbuddy.com/ch/1'), 'https://novelbuddy.com/ch/1');
    if (!('paragraphs' in r)) throw new Error('expected ok: ' + JSON.stringify(r));
    expect(r.paragraphs.length).toBe(2);
  });

  it('Madara theme extracts via CMS template with author', () => {
    const d = doc(MADARA, 'https://unknown-madara.example/novel/x/chapter-5');
    expect(extractAuthor(d)).toBe('Jane Doe');
    const r = extractChapter(d, 'https://unknown-madara.example/novel/x/chapter-5');
    if (!('paragraphs' in r)) throw new Error('expected ok: ' + JSON.stringify(r));
    expect(r.paragraphs.length).toBe(2);
    expect(r.author).toBe('Jane Doe');
    expect(r.fallback).toBeUndefined();
  });

  it('rule hit but empty falls back flagged', () => {
    const r = extractChapter(doc(RULE_ROT, 'https://novelbuddy.com/ch/1'), 'https://novelbuddy.com/ch/1');
    if (!('paragraphs' in r)) throw new Error('expected fallback ok: ' + JSON.stringify(r));
    expect(r.paragraphs.length).toBeGreaterThan(0);
    expect(r.fallback).toBe(true);
  });
});

describe('page stitch', () => {
  const url1 = 'https://example.com/novel/x/chapter-9';
  const body1 = P(`${LONG} on the docks where the gulls cried.`) + P('She drew her sword and faced the darkness gathering at the edge of the forest.');
  const body2 = P('He walked slowly through the dark marketplace, thinking of nothing at all in particular.') + P('The bell tolled midnight as the last candle flickered out in the draft.');

  function mockFetch(pages: Record<string, string>) {
    return vi.fn(async (u: unknown) => {
      const html = pages[String(u)];
      if (!html) throw new Error('blocked');
      return { ok: true, text: async () => html };
    });
  }

  it('stitches page 2 of the same chapter', async () => {
    const h1 = pageHtml('Chapter 9', body1, '/novel/x/chapter-9/2/');
    const h2 = pageHtml('Chapter 9', body2, null);
    const d1 = doc(h1, url1);
    const r = extractChapter(d1, url1);
    if (!('paragraphs' in r)) throw new Error('expected ok');
    expect(findPageNext(d1, url1)).toContain('/chapter-9/2/');
    const st = await stitchChapterPages(r as ExtractOk, d1, url1,
      mockFetch({ 'https://example.com/novel/x/chapter-9/2/': h2 }) as unknown as typeof fetch);
    expect(st.pages).toBe(2);
    expect(st.paragraphs.length).toBe(4);
  });

  it('stops when the next page has a different chapter title', async () => {
    const h1 = pageHtml('Chapter 9', body1, '/novel/x/chapter-10');
    const h2 = pageHtml('Chapter 10', body2, null);
    const d1 = doc(h1, url1);
    const r = extractChapter(d1, url1);
    if (!('paragraphs' in r)) throw new Error('expected ok');
    const st = await stitchChapterPages(r as ExtractOk, d1, url1,
      mockFetch({ 'https://example.com/novel/x/chapter-10': h2 }) as unknown as typeof fetch);
    expect(st.pages).toBe(1);
    expect(st.paragraphs.length).toBe(2);
  });

  it('failed fetch keeps page 1', async () => {
    const h1 = pageHtml('Chapter 9', body1, '/novel/x/chapter-9/?page=2');
    const d1 = doc(h1, url1);
    const r = extractChapter(d1, url1);
    if (!('paragraphs' in r)) throw new Error('expected ok');
    const st = await stitchChapterPages(r as ExtractOk, d1, url1,
      mockFetch({}) as unknown as typeof fetch);
    expect(st.pages).toBe(1);
  });
});
