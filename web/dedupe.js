'use strict';

// 小说等成批文件常出现「0-140」旧版与「0-180」新版的重复。
// 这里只依据文件名/体积/日期做保守判断，供下载前预览和剔除。

const EXT_RE = /\.([a-z0-9]{2,5})$/i;
const RANGE_RE = /(?:第\s*)?(\d{1,5})\s*(?:-|~|—|–|－|至|到|_)\s*(\d{1,5})\s*(?:章|话|节|集|回)?/g;
const NOISE_RE = /(?:排版|连载中|连载|已完结|完结|全本|全集|精校版|精校|校对版|校对|精排版|精排|无删减|最新|合集|打包|下载|珍藏版|典藏版)/gi;
const BRACKET_NOISE_RE = /[\[【(（]\s*(?:完结|全本|全集|精校|校对|精排|txt|下载|最新|合集|打包|连载|排版)\s*[\]】)）]/gi;
const AUTHOR_RE = /(?:作品)?作者\s*[:：]\s*(.+)$/;

function analyzeName(item) {
  let name = String(item.name || item.caption || '').trim();
  let ext = '';
  const extMatch = name.match(EXT_RE);
  if (extMatch) {
    ext = extMatch[1].toLowerCase();
    name = name.slice(0, extMatch.index);
  }
  let range = null;
  RANGE_RE.lastIndex = 0;
  let match;
  while ((match = RANGE_RE.exec(name))) {
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (end > start) {
      range = { start, end, raw: match[0], index: match.index };
    }
  }
  // 「《书名》01_187连载_作品作者：某某」把作者拆出来，避免不同作者的同名书被合并
  let author = '';
  const authorMatch = name.match(AUTHOR_RE);
  if (authorMatch) {
    author = authorMatch[1].replace(/[.\s]+$/, '').trim().toLowerCase();
    name = name.slice(0, authorMatch.index);
  }
  let key = name;
  if (range) {
    key = key.slice(0, range.index) + key.slice(range.index + range.raw.length);
  }
  key = key
    .replace(BRACKET_NOISE_RE, '')
    .replace(NOISE_RE, '')
    .replace(/[\[\]【】()（）{}<>《》「」]/g, ' ')
    .replace(/[\s._\-—–·、,，:：]+/g, '')
    .toLowerCase();
  if (author) key = `${key}|${author}`;
  return {
    key,
    ext,
    range,
    size: Number(item.size || 0),
    date: String(item.date || ''),
    id: Number(item.id),
  };
}

function coverage(meta) {
  return meta.range ? meta.range.end - meta.range.start : -1;
}

function rangeLabel(meta) {
  return meta.range ? `${meta.range.start}-${meta.range.end}` : '无区间';
}

function sameRange(a, b) {
  if (!a || !b) return !a && !b;
  return a.start === b.start && a.end === b.end;
}

function coveredBy(inner, outer) {
  return (
    !!inner.range &&
    !!outer.range &&
    inner.range.start >= outer.range.start &&
    inner.range.end <= outer.range.end &&
    coverage(outer) > coverage(inner)
  );
}

const ARCHIVE_EXTS = new Set(['rar', 'zip', '7z', 'tar', 'gz']);

function sameFamily(a, b) {
  if (a === b) return true;
  return ARCHIVE_EXTS.has(a) && ARCHIVE_EXTS.has(b);
}

/**
 * @param {Array} items 待判断的消息（需含 id/name/size/date）
 * @returns {{keep:number[], skip:Array, groups:number}}
 */
function planDedupe(items) {
  const groups = new Map();
  const keep = new Set();
  for (const item of items) {
    const meta = analyzeName(item);
    if (!meta.key) {
      keep.add(meta.id);
      continue;
    }
    if (!groups.has(meta.key)) groups.set(meta.key, []);
    groups.get(meta.key).push(meta);
  }

  const skipped = [];
  let groupCount = 0;
  for (const list of groups.values()) {
    if (list.length === 1) {
      keep.add(list[0].id);
      continue;
    }
    groupCount += 1;
    const ordered = [...list].sort(
      (a, b) =>
        coverage(b) - coverage(a) ||
        (a.date === b.date ? b.id - a.id : (a.date < b.date ? 1 : -1))
    );
    const winners = [];
    for (const cand of ordered) {
      const same = winners.find(
        (win) => win.ext === cand.ext && sameRange(win.range, cand.range)
      );
      if (same) {
        skipped.push({
          id: cand.id,
          name: (items.find((it) => Number(it.id) === cand.id) || {}).name || '',
          reason: same.range
            ? `与「${rangeLabel(same)}」同区间，保留较新的版本`
            : '同名重复，保留较新的版本',
          keeperName: '',
          keeperId: same.id,
        });
        continue;
      }
      const cover = winners.find((win) => sameFamily(win.ext, cand.ext) && coveredBy(cand, win));
      if (cover) {
        skipped.push({
          id: cand.id,
          name: (items.find((it) => Number(it.id) === cand.id) || {}).name || '',
          reason: `被「${rangeLabel(cover)}」完整覆盖`,
          keeperName: '',
          keeperId: cover.id,
        });
        continue;
      }
      winners.push(cand);
      keep.add(cand.id);
    }
  }

  const byId = new Map(items.map((it) => [Number(it.id), it]));
  const skip = skipped.map((entry) => ({
    id: entry.id,
    name: (byId.get(entry.id) || {}).name || entry.name,
    reason: entry.reason,
    keeperName: (byId.get(entry.keeperId) || {}).name || '',
  }));
  return { keep: [...keep], skip, groups: groupCount };
}

if (typeof window !== 'undefined') {
  window.TGDedupe = { analyzeName, planDedupe };
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { analyzeName, planDedupe };
}
