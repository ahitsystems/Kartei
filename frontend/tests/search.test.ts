import assert from 'node:assert/strict'
import { test } from 'node:test'
import { buildIndex, digits, matcher } from '../src/lib/search.ts'

test('phone normalization', () => {
  assert.equal(digits('+49 (511) 123-4'), '05111234')
  assert.equal(digits('0049 511 1234'), '05111234')
})

test('search matching', () => {
  const rec: any = { uid: '1', kind: 'person', first_name: 'Jörg', last_name: 'Müller', name: '', company: null,
    legacy_id: '', data: { tel: '+49 511 1234' } }
  const field: any = { key: 'tel', type: 'phone', searchable: true, archived: false, options: [] }
  const [ix] = buildIndex([rec], [field], new Map())
  for (const q of ['müller', 'muller', 'mueller', 'jörg mül', '0511 1234', '511/12']) assert.ok(matcher(q)(ix), q)
  for (const q of ['meier', 'müller 999']) assert.ok(!matcher(q)(ix), q)
})

test('list order and name display', async () => {
  const { compareBy, displayName } = await import('../src/lib/search.ts')
  const p = (first: string, last: string, updated: string, created: string) =>
    ({ kind: 'person', first_name: first, last_name: last, name: '', updated_at: updated, created_at: created }) as any
  const a = p('Zoë', 'Albers', '2026-01-02', '2025-01-01'), b = p('Anna', 'Zander', '2026-03-01', '2024-01-01'),
    c = p('Bert', 'Müller', '2026-02-01', '2026-01-01')
  const order = (s: any) => [a, b, c].sort(compareBy(s)).map((r) => r.last_name)
  assert.deepEqual(order('last_name'), ['Albers', 'Müller', 'Zander'])
  assert.deepEqual(order('first_name'), ['Zander', 'Müller', 'Albers'])
  assert.deepEqual(order('updated'), ['Zander', 'Müller', 'Albers'])
  assert.deepEqual(order('created'), ['Müller', 'Albers', 'Zander'])
  assert.equal(displayName(a), 'Zoë Albers')
  assert.equal(displayName(a, 'last_first'), 'Albers, Zoë')
  assert.equal(displayName({ ...a, first_name: '' }, 'last_first'), 'Albers')
})

test('Kölner Phonetik', async () => {
  const { koelner } = await import('../src/lib/search.ts')
  // reference values of the published algorithm
  assert.equal(koelner('Müller-Lüdenscheidt'), '65752682')
  assert.equal(koelner('Wikipedia'), '3412')
  assert.equal(koelner('Breschnew'), '17863')
  for (const group of [['Meier', 'Mayer', 'Maier', 'Meyer'], ['Schmidt', 'Schmitt', 'Schmid'], ['Müller', 'Mueller', 'Miller'],
                       ['Hoffmann', 'Hofmann'], ['Christoph', 'Kristof'], ['Philipp', 'Filip']])
    assert.equal(new Set(group.map(koelner)).size, 1, group.join(' '))
  assert.notEqual(koelner('Meier'), koelner('Müller'))
})

test('typo distance: one wrong, missing, extra or swapped letter', async () => {
  const { typoDistance } = await import('../src/lib/search.ts')
  assert.equal(typoDistance('fsicher', 'fischer', 1), 1)   // swapped
  assert.equal(typoDistance('schmidtt', 'schmidt', 1), 1)  // extra
  assert.equal(typoDistance('fiscer', 'fischer', 1), 1)    // missing
  assert.equal(typoDistance('weger', 'weber', 1), 1)       // wrong
  assert.equal(typoDistance('becker', 'weber', 1), 2)      // too far: max + 1
})

test('similar hits: sound and typos on names, only beyond the real hits', async () => {
  const { similarMatcher } = await import('../src/lib/search.ts')
  const person = (uid: string, first: string, last: string, company: string | null = null): any =>
    ({ uid, kind: 'person', first_name: first, last_name: last, name: '', company, legacy_id: '', data: {} })
  const firm: any = { uid: 'c', kind: 'company', first_name: '', last_name: '', name: 'Fischer Logistik', company: null, legacy_id: '', data: {} }
  const recs = [person('1', 'Anna', 'Meyer'), person('2', 'Jonas', 'Schmidt', 'c'), person('3', 'Ida', 'Becker'), firm]
  const byUid = new Map(recs.map((r) => [r.uid, r]))
  const ix = buildIndex(recs, [], byUid)
  const hits = (q: string) => { const m = similarMatcher(q); return m ? ix.filter(m).map((x) => x.rec.uid) : null }
  assert.deepEqual(hits('Maier'), ['1'])             // sounds like Meyer
  assert.deepEqual(hits('anna maier'), ['1'])        // "anna" matches as usual, "maier" by sound
  assert.deepEqual(hits('schmitt'), ['2'])
  assert.deepEqual(hits('fsicher'), ['c', '2'])      // typo of the company name: the company and its person (list order)
  assert.deepEqual(hits('meyer'), [])                // a real hit is not "similar" again
  assert.deepEqual(hits('maier becker'), [])         // every word has to fit
  assert.equal(hits('0511'), null)                   // numbers: exact only
  assert.equal(hits('mx'), null)                     // too short to guess
})
