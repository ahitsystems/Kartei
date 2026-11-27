<script lang="ts">
  // phone view: favorites as speed-dial tiles; a contact shows only its phone numbers, a company also its persons'
  import { Bookmark, History, Phone, Star, Users } from '@lucide/svelte'
  import Avatar from './Avatar.svelte'
  import { store } from '../lib/store.svelte'
  import { dateTime, isEmpty, relTime } from '../lib/format'
  import { L, t, terr } from '../lib/i18n.svelte'
  import { ApiError } from '../lib/api'
  import { toast } from '../lib/toast.svelte'
  import { telHref } from '../lib/tel'
  import { listTest, type Scope } from '../lib/listfilter'
  import { emptyGroup } from '../lib/advanced'
  import { calls, logCall } from '../lib/calls.svelte'
  import type { Rec } from '../lib/types'

  let { rec }: { rec?: Rec } = $props()

  const phones = (r: Rec) => store.fieldsFor(r.kind).filter((f) => f.type === 'phone' && !isEmpty(r.data[f.key]))
                                  .map((f) => ({ label: L(f.label), v: String(r.data[f.key]) }))
  const sub = (r: Rec) => (r.kind === 'person' ? store.companyName(r) : t('kind.company'))
  // speed-dial tiles: the favorites or a saved filter (own or shared), chosen per device
  let list = $state(localStorage.getItem('kartei:phone-view-list') ?? '')
  $effect(() => localStorage.setItem('kartei:phone-view-list', list))
  const saved = $derived(store.filters.find((f) => String(f.id) === list))
  const tiles = $derived.by(() => {
    if (!saved) return store.favorites.map((u) => store.byUid.get(u)).filter((r): r is Rec => !!r)
    const s = saved.state
    const test = listTest({ q: s.q, scope: s.scope as Scope, kind: s.kind, filters: Object.entries(s.filters).filter(([, v]) => v.length), adv: s.adv ?? emptyGroup() },
                          store.fields, store.user?.username, new Date(),
                          { review: new Set(store.reviews.map((r) => r.record)), proposals: new Set(store.proposals.flatMap((p) => (p.record ? [p.record] : []))) })
    return store.index.filter(test).map((x) => x.rec)
  })

  $effect(() => { if (rec) store.viewed(rec.uid) })

  const persons = $derived(rec?.kind === 'company' ? store.persons.get(rec.uid) ?? [] : [])
  const favorite = $derived(!!rec && store.favorites.includes(rec.uid))
  const toggleFavorite = () => rec && store.toggleFavorite(rec.uid)
    .catch((e) => toast(e instanceof ApiError ? terr(e.code) : t('err.generic'), { kind: 'error' }))
</script>

{#snippet tile(r: Rec)}
  <section class="tile card">
    <a class="who" href="/{r.kind}/{r.uid}"><Avatar rec={r} size={36} />
      <span><strong>{store.name(r)}</strong><span class="muted">{sub(r)}</span></span></a>
    {@render dial(r)}
  </section>
{/snippet}

{#snippet dial(r: Rec)}
  {#each phones(r) as p (p.label + p.v)}
    <a class="dial" href={telHref(p.v, store.tel)} onclick={() => logCall(r.uid, p.v, p.label)}><Phone size={18} /><span class="num tabular">{p.v}</span><span class="lbl">{p.label}</span></a>
  {:else}
    <p class="muted">{t('phoneview.no_phone')}</p>
  {/each}
{/snippet}

<div class="phone-view">
  {#if rec}
    <header>
      <Avatar {rec} size={56} />
      <div class="title"><h1>{store.name(rec) || '—'}</h1><div class="muted">{sub(rec)}</div></div>
      <button class="btn icon star" class:on={favorite} onclick={toggleFavorite} aria-pressed={favorite}
              title={favorite ? t('fav.remove') : t('fav.add')} aria-label={favorite ? t('fav.remove') : t('fav.add')}>
        <Star size={15} fill={favorite ? 'currentColor' : 'none'} /></button>
    </header>
    <div class="dials">{@render dial(rec)}</div>
    {#if persons.length}
      <h2 class="persons"><Users size={15} />{t('view.persons')}</h2>
      <div class="tiles">{#each persons as p (p.uid)}{@render tile(p)}{/each}</div>
    {/if}
  {:else}
    <div class="home">
      <div>
        <h2>{#if saved}<Bookmark size={15} />{:else}<Star size={15} />{/if}
          <select class="select" bind:value={list} aria-label={t('phoneview.list')}>
            <option value="">{t('dash.favorites')}</option>
            {#if store.filters.length}
              <optgroup label={t('saved.title')}>
                {#each store.filters as f (f.id)}<option value={String(f.id)}>{f.name}</option>{/each}
              </optgroup>
            {/if}
          </select></h2>
        <div class="tiles">
          {#each tiles as r (r.uid)}
            {@render tile(r)}
          {:else}
            <p class="muted">{saved ? t('common.no_matches') : t('dash.no_favorites')}</p>
          {/each}
        </div>
      </div>
      <aside>
        <h2><History size={15} />{t('phoneview.calls')}</h2>
        <ul class="calls card">
          {#each calls.list.slice(0, 10) as c (c.at)}
            {@const r = store.byUid.get(c.uid)}
            <li>
              {#if r}<a class="nm" href="/{r.kind}/{r.uid}">{store.name(r)}</a>{:else}<span class="nm muted">{c.uid ? '—' : t('phoneview.typed')}</span>{/if}
              <time class="muted" datetime={c.at} title={dateTime(c.at)}>{relTime(c.at)}</time>
              <a class="tabular" href={telHref(c.v, store.tel)} onclick={() => logCall(c.uid, c.v, c.label)}><Phone size={13} />{c.v}</a>
              <span class="muted lbl2">{c.label}</span>
            </li>
          {:else}
            <li class="muted">{t('phoneview.no_calls')}</li>
          {/each}
        </ul>
      </aside>
    </div>
  {/if}
</div>

<style>
  .phone-view { max-width: 1240px; margin: 0 auto; padding: 24px 28px; }
  header { display: flex; align-items: center; gap: 14px; margin-bottom: 20px; }
  .title { flex: 1; min-width: 0; }
  .star.on { color: var(--warn); }
  h1 { font-size: 24px; }
  h2 .select { height: 30px; width: auto; max-width: 320px; font-size: 13px; color: var(--text); }
  h2 { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--muted); margin-bottom: 10px; }
  h2.persons { margin-top: 26px; }
  .home { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 24px; align-items: start; }
  .calls { list-style: none; margin: 0; padding: 4px 12px; }
  .calls li { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
  .calls li:last-child { border-bottom: 0; }
  .calls .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
  .calls a { display: inline-flex; align-items: center; gap: 5px; }
  .calls time, .calls .lbl2 { font-size: 12px; text-align: right; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
  .tile { padding: 14px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .who { display: flex; align-items: center; gap: 10px; color: inherit; min-width: 0; }
  .who:hover { text-decoration: none; }
  .who > span { display: flex; flex-direction: column; min-width: 0; }
  .who strong, .who .muted { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dials { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
  .dial { display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 10px 16px; border-radius: 12px;
    background: var(--accent-soft); color: var(--accent-text); border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
    transition: background-color 0.12s; }
  .dial:hover { background: color-mix(in srgb, var(--accent) 22%, transparent); text-decoration: none; }
  .num { font-size: 18px; font-weight: 600; }
  .lbl { margin-left: auto; font-size: 12px; color: var(--muted); }
  .num, .lbl { white-space: nowrap; }
  /* favorite tiles are narrower: number and kind on one line */
  .tile .dial { min-height: 44px; padding: 8px 12px; gap: 8px; }
  .tile .num { font-size: 15px; }
</style>
