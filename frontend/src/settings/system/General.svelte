<script lang="ts">
  import { Phone } from '@lucide/svelte'
  import { ApiError, api, post } from '../../lib/api'
  import { store } from '../../lib/store.svelte'
  import { t, terr } from '../../lib/i18n.svelte'
  import { toast } from '../../lib/toast.svelte'
  import { telHref } from '../../lib/tel'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)
  const save = () => ctx.save()

  // one-time cleanup of numbers saved before normalization: count first, then apply
  let phoneRun = $state<{ count: number; sample: { uid: string; kind: string; name: string; old: string; new: string }[] } | null>(null)
  async function checkPhones() {
    if (await save()) phoneRun = await post('/api/admin/phones/normalize', { dry_run: true })
  }
  async function normalizePhones() {
    const r = await post<{ count: number }>('/api/admin/phones/normalize', { dry_run: false })
    toast(t('phone.done', { n: r.count }))
    phoneRun = null
    store.sync()
  }

  /** the organisation's own logo for the phone list: uploaded right away (not with Save), then chosen */
  async function uploadLogo(e: Event & { currentTarget: HTMLInputElement }) {
    const file = e.currentTarget.files?.[0]
    e.currentTarget.value = ''
    if (!file) return
    try {
      const r = await api<Record<string, any>>('PUT', '/api/admin/print-logo', file)
      s!.print_logo_v = r.print_logo_v
      s!.print_logo_mode = r.print_logo_mode
      store.printLogo = r.print_logo_mode === 'custom' ? r.print_logo_v : r.print_logo_mode
      toast(t('settings.saved'))
    } catch (err) {
      toast(err instanceof ApiError ? terr(err.code) : t('err.generic'), { kind: 'error' })
    }
  }
  async function removeLogo() {
    const r = await api<Record<string, any>>('DELETE', '/api/admin/print-logo')
    s!.print_logo_v = r.print_logo_v
    s!.print_logo_mode = r.print_logo_mode
    store.printLogo = r.print_logo_mode === 'custom' ? r.print_logo_v : r.print_logo_mode
  }
</script>

<div>
  <label class="label" for="lang">{t('general.default_language')}</label>
  <select id="lang" class="select" bind:value={s.default_language}>
    <option value="de">Deutsch</option><option value="en">English</option>
  </select>
</div>
<div>
  <label class="label" for="totp-req">{t('general.totp_required')}</label>
  <select id="totp-req" class="select" bind:value={s.totp_required}>
    {#each ['none', 'admins', 'all'] as v (v)}<option value={v}>{t(`general.totp_required_${v}`)}</option>{/each}
  </select>
  <p class="hint">{t('general.totp_required_hint')}</p>
</div>
<div>
  <label class="label" for="base">{t('general.base_url')}</label>
  <input id="base" class="input" bind:value={s.base_url} placeholder="https://kartei.example.de" />
  <p class="hint">{t('general.base_url_hint')}</p>
</div>
<div>
  <label class="label" for="mapurl">{t('general.map_url')}</label>
  <input id="mapurl" class="input mono" bind:value={s.map_url} placeholder="https://www.google.com/maps/search/?api=1&query={'{address}'}" />
  <p class="hint">{t('general.map_url_hint')}</p>
  {#if errors.map_url}<div class="error-text">{terr(errors.map_url)}</div>{/if}
</div>
<div>
  <span class="label">{t('general.print_logo')}</span>
  <div class="logo-choice" role="radiogroup" aria-label={t('general.print_logo')}>
    <label><input type="radio" bind:group={s.print_logo_mode} value="kartei" /> {t('general.print_logo_kartei')}</label>
    <label><input type="radio" bind:group={s.print_logo_mode} value="custom" disabled={!s.print_logo_v} />
      {t('general.print_logo_custom')}</label>
    <label><input type="radio" bind:group={s.print_logo_mode} value="none" /> {t('general.print_logo_none')}</label>
  </div>
  <div class="logo-row">
    {#if s.print_logo_v}<img class="logo-preview" src="/api/print-logo?v={s.print_logo_v}" alt={t('general.print_logo_custom')} />{/if}
    <label class="btn sm">{t('general.print_logo_upload')}
      <input type="file" accept="image/png,image/jpeg" hidden onchange={uploadLogo} /></label>
    {#if s.print_logo_v}
      <button class="btn sm danger" onclick={removeLogo}>{t('general.print_logo_remove')}</button>
    {/if}
  </div>
  <p class="hint">{t('general.print_logo_hint')}</p>
  {#if errors.print_logo_mode}<div class="error-text">{terr(errors.print_logo_mode)}</div>{/if}
</div>
<div class="row2">
  <div>
    <label class="label" for="trash">{t('general.trash_days')}</label>
    <input id="trash" class="input" type="number" min="0" bind:value={s.trash_days} />
    <p class="hint">{t('general.trash_hint')}</p>
    {#if errors.trash_days}<div class="error-text">{terr(errors.trash_days)}</div>{/if}
    <label class="check"><input type="checkbox" bind:checked={s.purge_anonymize} /> {t('general.purge_anonymize')}</label>
    <p class="hint">{t('general.purge_anonymize_hint')}</p>
  </div>
  <div>
    <label class="label" for="iv">{t('general.export_interval')}</label>
    <input id="iv" class="input" type="number" min="1" bind:value={s.export_interval_minutes} />
    {#if errors.export_interval_minutes}<div class="error-text">{terr(errors.export_interval_minutes)}</div>{/if}
    <label class="label" for="retention">{t('general.retention_months')}</label>
    <input id="retention" class="input" type="number" min="0" bind:value={s.retention_months} />
    <p class="hint">{t('general.retention_hint')}</p>
    {#if errors.retention_months}<div class="error-text">{terr(errors.retention_months)}</div>{/if}
  </div>
</div>
<h3>{t('phone.title')}</h3>
<label class="check"><input type="checkbox" bind:checked={s.phone_normalize} /> {t('phone.normalize')}</label>
<div>
  <label class="label" for="pcc">{t('phone.country_code')}</label>
  <input id="pcc" class="input narrow" bind:value={s.phone_country_code} placeholder="+49" />
  <p class="hint">{t('phone.hint')}</p>
  {#if errors.phone_country_code}<div class="error-text">{terr(errors.phone_country_code)}</div>{/if}
</div>
<div class="row2">
  <div><label class="label" for="telo">{t('phone.tel_outside')}</label>
    <input id="telo" class="input narrow" bind:value={s.tel_outside} placeholder="0" maxlength="4" />
    {#if errors.tel_outside}<div class="error-text">{terr(errors.tel_outside)}</div>{/if}</div>
  <div><label class="label" for="telp">{t('phone.tel_plus')}</label>
    <input id="telp" class="input narrow" bind:value={s.tel_plus} placeholder="00" maxlength="4" />
    {#if errors.tel_plus}<div class="error-text">{terr(errors.tel_plus)}</div>{/if}</div>
</div>
<p class="hint">{t('phone.tel_hint', { example: telHref(`${s.phone_country_code || '+49'} 511 1234567`, { outside: s.tel_outside ?? '', plus: s.tel_plus ?? '' }).slice(4) })}</p>
<div class="test"><button type="button" class="btn" onclick={checkPhones}><Phone size={15} />{t('phone.check')}</button></div>
{#if phoneRun}
  <div class="result info">
    <strong>{t('phone.found', { n: phoneRun.count })}</strong>
    {#each phoneRun.sample as x, i (i)}<div class="hint"><a href="/{x.kind}/{x.uid}">{x.name}</a>: {x.old} → {x.new}</div>{/each}
    {#if phoneRun.count}<div><button type="button" class="btn" onclick={normalizePhones}>{t('phone.apply', { n: phoneRun.count })}</button></div>{/if}
  </div>
{/if}

<style>
  .logo-choice { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 4px 0 8px; }
  .logo-choice label { display: flex; align-items: center; gap: 6px; }
  .logo-row { display: flex; align-items: center; gap: 10px; }
  .logo-preview { height: 32px; max-width: 160px; object-fit: contain; border: 1px solid var(--border); border-radius: 6px; padding: 2px; background: #fff; }
</style>
