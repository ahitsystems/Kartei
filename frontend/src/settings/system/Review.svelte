<script lang="ts">
  import { ClipboardCheck } from '@lucide/svelte'
  import { get, post } from '../../lib/api'
  import { store } from '../../lib/store.svelte'
  import { i18n, t, terr } from '../../lib/i18n.svelte'
  import { toast } from '../../lib/toast.svelte'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)
  const save = () => ctx.save()

  let reviewStats = $state<any>(null)
  const loadStats = () => get('/api/admin/reviews').then((r) => (reviewStats = r))
  loadStats()

  async function startReviews(scope: 'all' | 'due') {
    if (!(await save())) return
    const r = await post<any>('/api/admin/reviews', { scope }).catch(() => null)
    if (r) toast(t('review.started', { n: r.created, open: r.already_open, missing: r.no_responsible }))
    loadStats()
    store.sync()
  }
</script>

<p class="hint">{t('review.settings_hint')}</p>
<div class="modes">
  {#each ['off', 'rolling', 'campaign'] as m}
    <label class="check"><input type="radio" bind:group={s.review_mode} value={m} /> {t(`review.mode_${m}`)}</label>
  {/each}
</div>
{#if s.review_mode === 'rolling'}
  <div>
    <label class="label" for="rim">{t('review.interval')}</label>
    <input id="rim" class="input narrow" type="number" min="1" max="120" bind:value={s.review_interval_months} />
    {#if errors.review_interval_months}<div class="error-text">{terr(errors.review_interval_months)}</div>{/if}
  </div>
{:else if s.review_mode === 'campaign'}
  <div>
    <span class="label">{t('review.campaign_date')}</span>
    <div class="date">
      <input class="input narrow" type="number" min="1" max="31" bind:value={s.review_campaign_day} aria-label={t('review.day')} />
      <select class="select" bind:value={s.review_campaign_month} aria-label={t('review.month')}>
        {#each Array.from({ length: 12 }, (_, i) => i + 1) as m}
          <option value={m}>{new Intl.DateTimeFormat(i18n.lang, { month: 'long' }).format(new Date(2000, m - 1, 1))}</option>
        {/each}
      </select>
    </div>
    {#if errors.review_campaign_day || errors.review_campaign_month}<div class="error-text">{terr('invalid_number')}</div>{/if}
  </div>
{/if}
<div>
  <label class="label" for="rrd">{t('review.remind_days')}</label>
  <input id="rrd" class="input narrow" type="number" min="0" max="365" bind:value={s.review_remind_days} />
  <p class="hint">{t('review.remind_hint')}</p>
  {#if errors.review_remind_days}<div class="error-text">{terr(errors.review_remind_days)}</div>{/if}
</div>
{#if reviewStats}
  <div class="result info">
    <strong>{t('review.status')}</strong>
    <div>{t('review.stat_open', { n: reviewStats.open })}{#if reviewStats.unnotified}{' · '}{t('review.stat_unnotified', { n: reviewStats.unnotified })}{/if}{#if reviewStats.reminded}{' · '}{t('review.stat_reminded', { n: reviewStats.reminded })}{/if}</div>
    {#if s.review_mode === 'rolling'}<div>{t('review.stat_due', { n: reviewStats.due })}</div>{/if}
    {#if reviewStats.next_campaign}<div>{t('review.stat_next', { date: new Intl.DateTimeFormat(i18n.lang, { dateStyle: 'long' }).format(new Date(reviewStats.next_campaign)) })}</div>{/if}
    {#if reviewStats.no_responsible}
      <div class="err-line">{t('review.stat_missing', { n: reviewStats.no_responsible })}</div>
      <div class="missing">
        {#each reviewStats.no_responsible_sample as r (r.uid)}<a href="/{r.kind}/{r.uid}">{r.name || '—'}</a>{/each}
      </div>
    {/if}
  </div>
{/if}
<div class="test">
  {#if s.review_mode === 'rolling'}
    <button type="button" class="btn" onclick={() => startReviews('due')}><ClipboardCheck size={15} />{t('review.start_due')}</button>
  {/if}
  <button type="button" class="btn" onclick={() => startReviews('all')}><ClipboardCheck size={15} />{t('review.start_all')}</button>
</div>
<p class="hint">{t('review.mail_hint')}</p>
<h3>{t('review.proposals')}</h3>
<label class="check"><input type="checkbox" bind:checked={s.proposal_notify} /> {t('review.proposal_notify')}</label>
{#if s.proposal_notify}
  <div>
    <label class="label" for="pnt">{t('review.proposal_to')}</label>
    <input id="pnt" class="input" bind:value={s.proposal_notify_to} placeholder="team@example.de, chef@example.de" />
    <p class="hint">{t('review.proposal_to_hint')}</p>
    {#if errors.proposal_notify_to}<div class="error-text">{terr(errors.proposal_notify_to)}</div>{/if}
  </div>
{/if}

<style>
  .date { display: flex; gap: 8px; }
  .date .select { width: auto; }
  .missing { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 13px; }
</style>
