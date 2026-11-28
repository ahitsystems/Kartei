<script lang="ts">
  import { untrack } from 'svelte'
  import { PlugZap, RefreshCw } from '@lucide/svelte'
  import { ApiError, get, post } from '../../lib/api'
  import { t, terr, tmsg } from '../../lib/i18n.svelte'
  import { toast } from '../../lib/toast.svelte'
  import { dateTime, relTime } from '../../lib/format'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)
  const save = () => ctx.save()

  let testing = $state(false)
  let graphTest = $state<any>(null)
  const st = $derived(s.graph_status)
  let syncSince = $state<string | null>(untrack(() => s.graph_sync_requested)) // "sync now" requested at; cleared once a newer run is reported

  $effect(() => {
    if (!syncSince) return
    const since = Date.parse(syncSince)
    const id = setInterval(async () => {
      if (Date.now() - since > 15 * 60_000) return void (syncSince = null) // no result: stop waiting (is the exporter running?)
      const r = await get<Record<string, any>>('/api/admin/settings').catch(() => null)
      if (r?.graph_status?.at && Date.parse(r.graph_status.at) >= since) {
        s!.graph_status = r.graph_status
        syncSince = null
      }
    }, 3000)
    return () => clearInterval(id)
  })

  async function syncNow() {
    if (!(await save())) return
    try {
      syncSince = (await post<Record<string, any>>('/api/admin/graph/sync')).graph_sync_requested
    } catch (e) {
      toast(e instanceof ApiError ? terr(e.code) : t('err.generic'), { kind: 'error' })
    }
  }

  async function testGraph() {
    testing = true
    graphTest = null
    if (await save()) graphTest = await post('/api/admin/graph/test').catch(() => ({ error: t('err.generic') }))
    testing = false
  }
</script>

<p class="hint">{t('graph.hint')}</p>
<label class="check"><input type="checkbox" bind:checked={s.graph_enabled} /> {t('graph.enabled')}</label>
{#if errors.graph_enabled}<div class="error-text">{terr(errors.graph_enabled)}</div>{/if}
<div class="row2">
  <div><label class="label" for="gt">{t('graph.tenant')}</label><input id="gt" class="input mono" bind:value={s.graph_tenant_id} placeholder="00000000-0000-0000-0000-000000000000" /></div>
  <div><label class="label" for="gc">{t('graph.client')}</label><input id="gc" class="input mono" bind:value={s.graph_client_id} placeholder="00000000-0000-0000-0000-000000000000" /></div>
</div>
<div class="row2">
  <div><label class="label" for="gs">{t('graph.secret')}</label>
    <input id="gs" class="input" type="password" bind:value={ctx.secrets.graph_client_secret} placeholder={s.graph_client_secret_set ? '••••••••' : ''} autocomplete="new-password" /></div>
  <div><label class="label" for="gg">{t('graph.group')}</label><input id="gg" class="input mono" bind:value={s.graph_group_id} placeholder="00000000-0000-0000-0000-000000000000" /></div>
</div>
<div>
  <label class="label" for="gf">{t('graph.folder')}</label>
  <input id="gf" class="input" bind:value={s.graph_folder} />
  <p class="hint">{t('graph.folder_hint')}</p>
  {#if errors.graph_folder}<div class="error-text">{terr(errors.graph_folder)}</div>{/if}
</div>
<div class="row2">
  <div><label class="label" for="gfr">{t('graph.folder_responsible')}</label>
    <input id="gfr" class="input" bind:value={s.graph_folder_responsible} placeholder={t('graph.folder_off')} />
    {#if errors.graph_folder_responsible}<div class="error-text">{terr(errors.graph_folder_responsible)}</div>{/if}</div>
  <div><label class="label" for="gfi">{t('graph.folder_internal')}</label>
    <input id="gfi" class="input" bind:value={s.graph_folder_internal} placeholder={t('graph.folder_off')} />
    {#if errors.graph_folder_internal}<div class="error-text">{terr(errors.graph_folder_internal)}</div>{/if}</div>
</div>
<p class="hint">{t('graph.personal_hint')}</p>
<div>
  <label class="label" for="gfp">{t('graph.filter_prefix')}</label>
  <input id="gfp" class="input" bind:value={s.graph_filter_prefix} maxlength="50" />
  <p class="hint">{t('graph.filter_prefix_hint')}</p>
</div>
<div class="test">
  <button type="button" class="btn" onclick={testGraph} disabled={testing}><PlugZap size={15} />{t('graph.test')}</button>
  <button type="button" class="btn" class:spin={syncSince} onclick={syncNow} disabled={!!syncSince || !s.graph_enabled}><RefreshCw size={15} />{t('graph.sync_now')}</button>
</div>
{#if graphTest}
  <div class="result" class:bad={tmsg(graphTest.error)}>
    {#if graphTest.error}<strong>{t('graph.failed')}:</strong> {tmsg(graphTest.error)}
    {:else}<div>✓ {t('graph.test_ok', { n: graphTest.mailboxes })}</div>
      {#each graphTest.sample as mail}<div class="hint">{mail}</div>{/each}{/if}
  </div>
{/if}
<div class="result" class:info={!st?.error} class:bad={st?.error}>
  <strong>{t('graph.last')}</strong>
  {#if syncSince}<div>{t('graph.syncing')}</div>{/if}
  {#if !st?.at}<div>{t('graph.never')}</div>
  {:else}
    <div title={dateTime(st.at)}>{relTime(st.at)}</div>
    {#if st.error}<div>{tmsg(st.error)}</div>
    {:else}<div>{t('graph.result', { photos: 0, ...st })}</div>
      {#each st.failed as f}<div class="err-line">{tmsg(f)}</div>{/each}{/if}
  {/if}
</div>
<div class="result info">
  <strong>{t('graph.setup')}</strong>
  <div class="hint">1. {t('graph.setup_app')}</div>
  <div class="hint">2. {t('graph.setup_scope')}</div>
  <div class="hint">{t('graph.fields_hint')}</div>
</div>
