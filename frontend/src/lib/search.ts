import type { Field, Rec } from './types'

const CHOICE = ['select', 'multiselect', 'user', 'users'] // option labels are searchable too
const EXPAND: Record<string, string> = { ä: 'ae', ö: 'oe', ü: 'ue', ß: 'ss' }

/** Lowercase + diacritics stripped. "Lübeck" -> "lubeck" */
export const fold = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss')
/** Lowercase + german transliteration. "Lübeck" -> "luebeck" */
export const expand = (s: string) => s.toLowerCase().replace(/[äöüß]/g, (c) => EXPAND[c])

/** "+49 (511) 123-4" -> "05111234", "0511/1234" -> "05111234" */
export function digits(s: string) {
  let d = s.replace(/[^\d+]/g, '')
  if (d.startsWith('+49')) d = '0' + d.slice(3)
  else if (d.startsWith('0049')) d = '0' + d.slice(4)
  return d.replace(/\+/g, '00')
}

export interface Indexed {
  rec: Rec
  hay: string
  tel: string
  /** name words (person, company), folded, and their Kölner Phonetik codes: for similar hits */
  names: string[]
  codes: string[]
}

export type ListSort = 'last_name' | 'first_name' | 'updated' | 'created'
export type NameOrder = 'first_last' | 'last_first'

/** "Anna Müller", or with last_first "Müller, Anna"; companies by their name */
export function displayName(r: Rec, order: NameOrder = 'first_last') {
  if (r.kind === 'company') return r.name
  return order === 'last_first' ? [r.last_name, r.first_name].filter(Boolean).join(', ') : `${r.first_name} ${r.last_name}`.trim()
}

const collator = new Intl.Collator('de', { sensitivity: 'base', numeric: true })
/** the list order: alphabetical by last or first name (companies by name), or the newest change / contact first */
export function compareBy(sort: ListSort = 'last_name'): (a: Rec, b: Rec) => number {
  const name = (r: Rec) => r.kind === 'company' ? r.name
    : sort === 'first_name' ? `${r.first_name} ${r.last_name}` : `${r.last_name} ${r.first_name}`
  const alpha = (a: Rec, b: Rec) => collator.compare(name(a), name(b))
  if (sort === 'updated') return (a, b) => b.updated_at.localeCompare(a.updated_at) || alpha(a, b)
  if (sort === 'created') return (a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '') || alpha(a, b)
  return alpha
}

export function buildIndex(records: Rec[], fields: Field[], byUid: Map<string, Rec>, sort: ListSort = 'last_name'): Indexed[] {
  const searchable = fields.filter((f) => f.searchable && !f.archived)
  const cmp = compareBy(sort)
  return records
    .map((rec) => {
      const parts = [rec.first_name, rec.last_name, rec.name, rec.legacy_id]
      const company = rec.company ? byUid.get(rec.company)?.name ?? '' : ''
      if (rec.company) parts.push(company)
      const names = nameWords(rec, company)
      const tel: string[] = []
      for (const f of searchable) {
        const v = rec.data[f.key]
        if (v == null || v === '') continue
        if (f.type === 'phone') tel.push(digits(String(v)))
        if (f.type === 'contact') {
          const linked = byUid.get(v)
          parts.push(linked ? displayName(linked) : String(v))
          continue
        }
        if (CHOICE.includes(f.type)) {
          for (const x of Array.isArray(v) ? v : [v]) {
            const o = f.options.find((o) => o.value === x)
            parts.push(x, o?.de ?? '', o?.en ?? '')
          }
        } else parts.push(String(v))
      }
      const text = parts.join(' ')
      return { rec, hay: fold(text) + ' ' + expand(text), tel: tel.join(' '), names, codes: names.map(koelner) }
    })
    .sort((a, b) => cmp(a.rec, b.rec))
}

/** All tokens must match (AND). Digit tokens also match normalized phone numbers. */
export function matcher(query: string) {
  const tokens = query.trim().split(/\s+/).filter(Boolean)
  if (!tokens.length) return () => true
  const prepared = tokens.map((tok) => ({ text: fold(tok), tel: /^[\d+()/-]{3,}$/.test(tok) ? digits(tok) : '' }))
  return (x: Indexed) => prepared.every((p) => x.hay.includes(p.text) || (p.tel && x.tel.includes(p.tel)))
}

// ------------------------------------------------------------- similar names (phone view, reception)
// Names are heard, not read: "Meier" should find Meyer, "Fsicher" Fischer. Shown after the real hits, never part of
// them (counts, exports, bulk edit and saved filters stay exact). Only names, so the noise stays small.

/** Kölner Phonetik: German sound code of a word. Meier, Mayer, Maier, Meyer -> "67"; Schmidt, Schmitt -> "862" */
export function koelner(word: string): string {
  const s = fold(word).replace(/[^a-z]/g, '')
  let out = ''
  for (let i = 0; i < s.length; i++) {
    const c = s[i], prev = s[i - 1] ?? '', next = s[i + 1] ?? ''
    let d = ''
    if ('aeijouy'.includes(c)) d = '0'
    else if (c === 'h') d = ''
    else if (c === 'b') d = '1'
    else if (c === 'p') d = next === 'h' ? '3' : '1'
    else if (c === 'd' || c === 't') d = 'csz'.includes(next) && next ? '8' : '2'
    else if ('fvw'.includes(c)) d = '3'
    else if ('gkq'.includes(c)) d = '4'
    else if (c === 'c') {
      d = i === 0 ? ('ahkloqrux'.includes(next) && next ? '4' : '8')
        : 'sz'.includes(prev) || !('ahkoqux'.includes(next) && next) ? '8' : '4'
    } else if (c === 'x') d = 'ckq'.includes(prev) && prev ? '8' : '48'
    else if (c === 'l') d = '5'
    else if (c === 'm' || c === 'n') d = '6'
    else if (c === 'r') d = '7'
    else if (c === 's' || c === 'z') d = '8'
    out += d
  }
  // collapse repeats, then drop the vowel 0s except a leading one
  const collapsed = out.replace(/(.)\1+/g, '$1')
  return collapsed[0] + collapsed.slice(1).replace(/0/g, '')
}

/** Optimal string alignment distance (Levenshtein + swapped neighbours), stops early once above max */
export function typoDistance(a: string, b: string, max: number): number {
  if (Math.abs(a.length - b.length) > max) return max + 1
  let prev2: number[] = [], prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    const cur = [i]
    let best = i
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, prev2[j - 2] + 1)
      cur.push(v)
      best = Math.min(best, v)
    }
    if (best > max) return max + 1
    prev2 = prev
    prev = cur
  }
  return prev[b.length]
}

/** words of a record's names (person, company), folded: what similar matching compares against */
export function nameWords(r: Rec, companyName = ''): string[] {
  return fold([r.first_name, r.last_name, r.name, companyName].join(' ')).split(/[^a-z0-9]+/).filter((w) => w.length > 1)
}

/** Test for "similar" hits: every word of the query is in the record (like the normal search) or sounds like / is
 *  one typo away from one of its name words, and at least one only similar. Words under 3 letters and numbers
 *  have to match exactly. null = the query has nothing to be similar about. */
export function similarMatcher(query: string): ((x: Indexed) => boolean) | null {
  const tokens = query.trim().split(/\s+/).filter(Boolean).map((tok) => {
    const f = fold(tok)
    const word = /^[a-z]{3,}$/.test(f)
    return { f, exact: matcher(tok), code: word ? koelner(f) : '', max: !word ? -1 : f.length >= 8 ? 2 : f.length >= 4 ? 1 : 0 }
  })
  if (!tokens.some((t) => t.code)) return null
  const like = (t: (typeof tokens)[number], x: Indexed, i: number) =>
    (t.code.length > 1 && x.codes[i] === t.code) || (t.max > 0 && typoDistance(t.f, x.names[i], t.max) <= t.max)
  return (x) => {
    let similar = false
    for (const t of tokens) {
      if (t.exact(x)) continue
      if (!t.code || !x.names.some((_, i) => like(t, x, i))) return false
      similar = true
    }
    return similar
  }
}
