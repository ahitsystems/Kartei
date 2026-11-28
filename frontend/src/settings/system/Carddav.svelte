<script lang="ts">
  import { untrack } from 'svelte'
  import { Copy, KeyRound, PlugZap, RefreshCw } from '@lucide/svelte'
  import { ApiError, get, post } from '../../lib/api'
  import { t, terr, tmsg } from '../../lib/i18n.svelte'
  import { copyText, toast } from '../../lib/toast.svelte'
  import { dateTime, relTime } from '../../lib/format'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)
  const save = () => ctx.save()

  let testing = $state(false)
  let newCardPw = $state('')
  async function genCardPw() {
    const r = await post<{ password: string }>('/api/admin/carddav/password')
    newCardPw = r.password
    s!.carddav_password_set = true
  }

  // CardDAV relay in the DMZ: same "sync now" polling as the Outlook sync
  let relayTest = $state<any>(null)
  let relaySince = $state<string | null>(untrack(() => s.carddav_relay_requested))
  $effect(() => {
    if (!relaySince) return
    const since = Date.parse(relaySince)
    const id = setInterval(async () => {
      if (Date.now() - since > 15 * 60_000) return void (relaySince = null) // no result: stop waiting (is the exporter running?)
      const r = await get<Record<string, any>>('/api/admin/settings').catch(() => null)
      if (r?.carddav_relay_status?.at && Date.parse(r.carddav_relay_status.at) >= since) {
        s!.carddav_relay_status = r.carddav_relay_status
        relaySince = null
      }
    }, 3000)
    return () => clearInterval(id)
  })
  async function genRelayToken() {
    const r = await post<Record<string, any>>('/api/admin/carddav/relay-token')
    s!.carddav_relay_token_sha256 = r.carddav_relay_token_sha256 // keep unsaved edits in the form
  }
  async function testRelay() {
    testing = true
    relayTest = null
    if (await save()) relayTest = await post('/api/admin/carddav/relay-test').catch(() => ({ error: t('err.generic') }))
    testing = false
  }
  async function relaySync() {
    if (!(await save())) return
    try {
      relaySince = (await post<Record<string, any>>('/api/admin/carddav/relay-sync')).carddav_relay_requested
    } catch (e) {
      toast(e instanceof ApiError ? terr(e.code) : t('err.generic'), { kind: 'error' })
    }
  }

  const davUrl = $derived(`${(s?.carddav_relay_enabled && s?.carddav_relay_url) || s?.base_url || location.origin}`.replace(/\/$/, '') + '/carddav/')
  const copy = copyText
</script>

<label class="check"><input type="checkbox" bind:checked={s.carddav_enabled} /> {t('carddav.enabled')}</label>
<div class="row2">
  <div><label class="label" for="cu">{t('login.username')}</label><input id="cu" class="input" bind:value={s.carddav_username} /></div>
  <div>
    <span class="label">{t('login.password')}</span>
    <button type="button" class="btn" onclick={genCardPw}><KeyRound size={15} />{s.carddav_password_set ? t('carddav.regen') : t('carddav.gen')}</button>
  </div>
</div>
{#if newCardPw}
  <div class="result">
    {t('carddav.once')}<br />
    <code>{newCardPw}</code> <button type="button" class="btn sm icon ghost" onclick={() => copy(newCardPw)}><Copy size={13} /></button>
  </div>
{/if}
<h3>{t('carddav.relay')}</h3>
<p class="hint">{t('carddav.relay_hint')}</p>
<label class="check"><input type="checkbox" bind:checked={s.carddav_relay_enabled} /> {t('carddav.relay_enabled')}</label>
{#if errors.carddav_relay_enabled}<div class="error-text">{terr(errors.carddav_relay_enabled)}</div>{/if}
{#if s.carddav_relay_enabled && s.carddav_enabled}<div class="err-line">{t('carddav.relay_builtin_on')}</div>{/if}
<div>
  <label class="label" for="rurl">{t('carddav.relay_url')}</label>
  <input id="rurl" class="input" bind:value={s.carddav_relay_url} placeholder="https://carddav.example.de" />
  {#if errors.carddav_relay_url}<div class="error-text">{terr(errors.carddav_relay_url)}</div>{/if}
</div>
<div>
  <span class="label">{t('carddav.relay_token')}</span>
  <div class="test">
    <button type="button" class="btn" onclick={genRelayToken}><KeyRound size={15} />{s.carddav_relay_token_sha256 ? t('carddav.relay_token_regen') : t('carddav.relay_token_gen')}</button>
  </div>
  {#if s.carddav_relay_token_sha256}
    <p class="hint">{t('carddav.relay_token_hint')}</p>
    <div><code>{s.carddav_relay_token_sha256}</code> <button type="button" class="btn sm icon ghost" onclick={() => copy(s!.carddav_relay_token_sha256)}><Copy size={13} /></button></div>
  {/if}
</div>
<label class="check"><input type="checkbox" bind:checked={s.carddav_relay_tls_verify} /> {t('carddav.relay_tls_verify')}</label>
{#if s.carddav_relay_tls_verify}
  <div>
    <label class="label" for="rca">{t('carddav.relay_ca')}</label>
    <textarea id="rca" class="input mono" rows="3" bind:value={s.carddav_relay_ca_cert} placeholder="-----BEGIN CERTIFICATE-----"></textarea>
    {#if errors.carddav_relay_ca_cert}<div class="error-text">{terr(errors.carddav_relay_ca_cert)}</div>{/if}
  </div>
{/if}
<div>
  <label class="label" for="rguard">{t('ad.guard')}</label>
  <input id="rguard" class="input narrow" type="number" min="0" max="100" bind:value={s.carddav_relay_max_delete_percent} />
  <p class="hint">{t('ad.guard_hint')}</p>
  {#if errors.carddav_relay_max_delete_percent}<div class="error-text">{terr(errors.carddav_relay_max_delete_percent)}</div>{/if}
</div>
<div class="test">
  <button type="button" class="btn" onclick={testRelay} disabled={testing || !s.carddav_relay_url}><PlugZap size={15} />{t('carddav.relay_test')}</button>
  <button type="button" class="btn" class:spin={relaySince} onclick={relaySync} disabled={!!relaySince || !s.carddav_relay_enabled}><RefreshCw size={15} />{t('graph.sync_now')}</button>
</div>
{#if relayTest}
  <div class="result" class:bad={relayTest.error}>
    {#if relayTest.error}<strong>{t('graph.failed')}:</strong> {tmsg(relayTest.error)}{:else}✓ {t('carddav.relay_test_ok', relayTest)}{/if}
  </div>
{/if}
{#if s.carddav_relay_enabled || s.carddav_relay_status?.at}
  {@const rs = s.carddav_relay_status}
  <div class="result" class:info={!rs?.error} class:bad={rs?.error}>
    <strong>{t('graph.last')}</strong>
    {#if relaySince}<div>{t('graph.syncing')}</div>{/if}
    {#if !rs?.at}<div>{t('graph.never')}</div>
    {:else}
      <div title={dateTime(rs.at)}>{relTime(rs.at)}</div>
      {#if rs.error}<div>{tmsg(rs.error)}</div>{:else}<div>{t('carddav.relay_result', rs)}</div>{/if}
    {/if}
  </div>
{/if}
<div class="result info">
  <strong>{t('carddav.setup')}</strong>
  <div>{t('carddav.server')}: <code>{davUrl}</code> <button type="button" class="btn sm icon ghost" onclick={() => copy(davUrl)}><Copy size={13} /></button></div>
  <div class="hint">{t('carddav.ios')}</div>
  <div class="hint">{t('carddav.android')}</div>
  <div class="hint">{t('carddav.fields_hint')}</div>
</div>
