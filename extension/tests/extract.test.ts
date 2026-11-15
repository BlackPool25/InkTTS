import { describe, expect, it } from 'vitest';
import { extractChapter, extractTitle, findChapterUrl, setRuleOverrides } from '../entrypoints/content/extract';

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
