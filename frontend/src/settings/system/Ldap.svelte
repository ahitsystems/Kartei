<script lang="ts">
  import { PlugZap, UserPlus } from '@lucide/svelte'
  import { post } from '../../lib/api'
  import { t, terr, tmsg } from '../../lib/i18n.svelte'
  import { dateTime, relTime } from '../../lib/format'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)
  const save = () => ctx.save()

  let testUser = $state('')
  let testResult = $state<any>(null)
  let testing = $state(false)
  const us = $derived(s.ldap_users_status)

  async function test() {
    testing = true
    testResult = null
    await save()
    testResult = await post('/api/admin/ldap/test', { username: testUser }).catch(() => ({ error: t('err.generic') }))
    testing = false
  }

  async function importUsers() {
    testing = true
    if (await save()) s!.ldap_users_status = await post('/api/admin/ldap/import-users').catch(() => ({ error: t('err.generic') }))
    testing = false
  }
</script>

<p class="hint">{t('ldap.hint')}</p>
<div>
  <label class="label" for="uri">{t('ldap.uri')}</label>
  <input id="uri" class="input" bind:value={s.ldap_uri} placeholder="ldaps://dc01.example.local" />
</div>
<label class="check"><input type="checkbox" bind:checked={s.ldap_start_tls} /> StartTLS</label>
<div class="row2">
  <div><label class="label" for="bdn">{t('ldap.bind_dn')}</label><input id="bdn" class="input" bind:value={s.ldap_bind_dn} placeholder="CN=svc-kartei,OU=Service,DC=example,DC=local" /></div>
  <div><label class="label" for="bpw">{t('ldap.bind_pw')}</label>
    <input id="bpw" class="input" type="password" bind:value={ctx.secrets.ldap_bind_password} placeholder={s.ldap_bind_password_set ? '••••••••' : ''} autocomplete="new-password" /></div>
</div>
<div class="row2">
  <div><label class="label" for="base">{t('ldap.user_base')}</label><input id="base" class="input" bind:value={s.ldap_user_base} placeholder="OU=Users,DC=example,DC=local" /></div>
  <div><label class="label" for="flt">{t('ldap.user_filter')}</label><input id="flt" class="input" bind:value={s.ldap_user_filter} />
    {#if errors.ldap_user_filter}<div class="error-text">{terr(errors.ldap_user_filter)}</div>{/if}</div>
</div>
<h3>{t('ldap.groups')}</h3>
{#each ['admin', 'maintainer', 'read'] as role}
  <div><label class="label" for="g-{role}">{t(`role.${role}`)}</label>
    <input id="g-{role}" class="input" bind:value={s[`ldap_group_${role}`]} placeholder="CN=Kartei-{role},OU=Groups,DC=example,DC=local" /></div>
{/each}
<div>
  <label class="label" for="ca">{t('ldap.ca')}</label>
  <textarea id="ca" class="input mono" rows="4" bind:value={s.ldap_ca_cert} placeholder="-----BEGIN CERTIFICATE-----"></textarea>
</div>
<div class="test">
  <input class="input" bind:value={testUser} placeholder={t('ldap.test_user')} />
  <button type="button" class="btn" onclick={test} disabled={testing || !s.ldap_uri}><PlugZap size={15} />{t('ldap.test')}</button>
</div>
{#if testResult}
  <div class="result" class:bad={testResult.error || !testResult.bind}>
    {#if testResult.error}<strong>{t('ldap.failed')}:</strong> {tmsg(testResult.error)}
    {:else}
      <div>✓ {t('ldap.bind_ok')}</div>
      {#if testUser}
        {#if testResult.user}
          <div>✓ {testResult.user.dn}</div>
          <div>{t('ldap.role')}: <strong>{testResult.user.role ? t(`role.${testResult.user.role}`) : t('ldap.no_role')}</strong></div>
        {:else}<div>✗ {t('ldap.user_not_found')}</div>{/if}
      {/if}
    {/if}
  </div>
{/if}
<h3>{t('ldap.import')}</h3>
<p class="hint">{t('ldap.import_hint')}</p>
<div>
  <label class="label" for="ldg">{t('ldap.import_guard')}</label>
  <input id="ldg" class="input narrow" type="number" min="0" max="100" bind:value={s.ldap_users_max_deactivate_percent} />
  <p class="hint">{t('ldap.import_guard_hint')}</p>
  {#if errors.ldap_users_max_deactivate_percent}<div class="error-text">{terr(errors.ldap_users_max_deactivate_percent)}</div>{/if}
</div>
<div class="test">
  <button type="button" class="btn" onclick={importUsers} disabled={testing || !s.ldap_uri}><UserPlus size={15} />{t('ldap.import_now')}</button>
</div>
{#if us?.at}
  <div class="result" class:info={!us.error} class:bad={us.error}>
    <div title={dateTime(us.at)}>{t('graph.last')}: {relTime(us.at)}</div>
    {#if us.error}<div>{tmsg(us.error)}</div>{:else}<div>{t('ldap.import_result', us)}</div>{/if}
  </div>
{/if}
