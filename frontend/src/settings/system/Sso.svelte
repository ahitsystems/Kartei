<script lang="ts">
  import { Copy, KeyRound } from '@lucide/svelte'
  import { post } from '../../lib/api'
  import { t, terr } from '../../lib/i18n.svelte'
  import { copyText } from '../../lib/toast.svelte'
  import Combobox from '../../components/Combobox.svelte'
  import { dateTime, relTime } from '../../lib/format'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)

  // single sign-on: the identity provider gets the SCIM URL + token and the redirect URI
  let scimToken = $state('')
  const origin = $derived((s?.base_url || location.origin).replace(/\/$/, ''))
  const scimUrl = $derived(`${origin}/scim/v2`)
  const redirectUri = $derived(`${origin}/api/oidc/callback`)
  async function genScimToken() {
    scimToken = (await post<{ token: string }>('/api/admin/scim/token')).token
    s!.scim_token_set = true
  }
  const copy = copyText

  // Windows sign-in: the keytab goes up as base64 with the next save, like the other secrets
  const spn = $derived(`HTTP/${new URL(origin).hostname}`)
  async function pickKeytab(e: Event) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0]
    ctx.secrets.kerberos_keytab = f ? btoa(String.fromCharCode(...new Uint8Array(await f.arrayBuffer()))) : ''
  }
</script>

<p class="hint">{t('sso.intro')}</p>
<h3>{t('sso.scim')}</h3>
<p class="hint">{t('sso.scim_hint')}</p>
<div>
  <label class="label" for="scim-url">{t('sso.scim_url')}</label>
  <div class="test">
    <input id="scim-url" class="input mono" readonly value={scimUrl} />
    <button type="button" class="btn icon" title={t('view.copy')} aria-label={t('view.copy')} onclick={() => copy(scimUrl)}><Copy size={15} /></button>
  </div>
</div>
<div class="test">
  <button type="button" class="btn" onclick={genScimToken}><KeyRound size={15} />{t(s.scim_token_set ? 'sso.scim_regen' : 'sso.scim_gen')}</button>
  {#if s.scim_token_set}<span class="hint" title={s.scim_seen_at ? dateTime(s.scim_seen_at) : ''}>{t('sso.scim_seen')}: {s.scim_seen_at ? relTime(s.scim_seen_at) : t('sso.scim_never')}</span>{/if}
</div>
{#if scimToken}
  <div class="result">
    <div>{t('sso.scim_token_once')}</div>
    <div class="test"><code>{scimToken}</code>
      <button type="button" class="btn sm icon" title={t('view.copy')} aria-label={t('view.copy')} onclick={() => copy(scimToken)}><Copy size={14} /></button></div>
  </div>
{/if}
<h3>{t('sso.groups')}</h3>
<p class="hint">{s.scim_groups.length ? t('sso.groups_hint') : t('sso.no_groups')}</p>
{#each ['admin', 'maintainer', 'read'] as role}
  <div><label class="label" for="sg-{role}">{t(`role.${role}`)}</label>
    <Combobox id="sg-{role}" value={s[`scim_group_${role}`] || null} invalid={!!errors[`scim_group_${role}`]}
      options={s.scim_groups.map((g: any) => ({ value: g.id, label: g.name, hint: t('sso.members', { n: g.members }) }))}
      onchange={(v) => (s![`scim_group_${role}`] = v ?? '')} /></div>
{/each}
<h3>{t('sso.oidc')}</h3>
<label class="check"><input type="checkbox" bind:checked={s.oidc_enabled} /> {t('sso.enabled')}</label>
{#if errors.oidc_enabled}<div class="error-text">{terr(errors.oidc_enabled)}</div>{/if}
<div>
  <label class="label" for="iss">{t('sso.issuer')}</label>
  <input id="iss" class="input" class:invalid={errors.oidc_issuer} bind:value={s.oidc_issuer} placeholder="https://login.microsoftonline.com/<tenant-id>/v2.0" />
  <p class="hint">{t('sso.issuer_hint')}</p>
  {#if errors.oidc_issuer}<div class="error-text">{terr(errors.oidc_issuer)}</div>{/if}
</div>
<div class="row2">
  <div><label class="label" for="cid">{t('sso.client_id')}</label><input id="cid" class="input" bind:value={s.oidc_client_id} /></div>
  <div><label class="label" for="csec">{t('sso.client_secret')}</label>
    <input id="csec" class="input" type="password" bind:value={ctx.secrets.oidc_client_secret} placeholder={s.oidc_client_secret_set ? '••••••••' : ''} autocomplete="new-password" /></div>
</div>
<div class="row2">
  <div><label class="label" for="scopes">{t('sso.scopes')}</label><input id="scopes" class="input" class:invalid={errors.oidc_scopes} bind:value={s.oidc_scopes} />
    {#if errors.oidc_scopes}<div class="error-text">{terr(errors.oidc_scopes)}</div>{/if}</div>
  <div><label class="label" for="claim">{t('sso.claim')}</label>
    <select id="claim" class="select" bind:value={s.oidc_claim}>
      {#each ['preferred_username', 'upn', 'email'] as c}<option value={c}>{c}</option>{/each}
    </select></div>
</div>
<p class="hint">{t('sso.claim_hint')}</p>
<div>
  <label class="label" for="olabel">{t('sso.label')}</label>
  <input id="olabel" class="input" bind:value={s.oidc_label} placeholder={t('sso.button')} maxlength="60" />
</div>
<div>
  <label class="label" for="redir">{t('sso.redirect')}</label>
  <div class="test">
    <input id="redir" class="input mono" readonly value={redirectUri} />
    <button type="button" class="btn icon" title={t('view.copy')} aria-label={t('view.copy')} onclick={() => copy(redirectUri)}><Copy size={15} /></button>
  </div>
</div>
<div>
  <label class="check"><input type="checkbox" bind:checked={s.oidc_mfa_trusted} /> {t('sso.mfa_trusted')}</label>
  <p class="hint">{t('sso.mfa_trusted_hint')}</p>
</div>

<h3>{t('kerberos.title')}</h3>
<p class="hint">{t('kerberos.hint')}</p>
<label class="check"><input type="checkbox" bind:checked={s.kerberos_enabled} /> {t('kerberos.enabled')}</label>
{#if errors.kerberos_enabled}<div class="error-text">{terr(errors.kerberos_enabled)}</div>{/if}
<div>
  <label class="label" for="keytab">{t('kerberos.keytab')}</label>
  <input id="keytab" class="input" class:invalid={errors.kerberos_keytab} type="file" accept=".keytab" onchange={pickKeytab} />
  {#if errors.kerberos_keytab}<div class="error-text">{terr(errors.kerberos_keytab)}</div>{/if}
  {#if s.kerberos_keytab_set && ctx.secrets.kerberos_keytab === ''}
    <div class="test">
      <p class="hint">{t('kerberos.keytab_set')}</p>
      <button type="button" class="btn" onclick={() => { ctx.secrets.kerberos_keytab = null; s.kerberos_enabled = false }}>{t('kerberos.keytab_remove')}</button>
    </div>
  {:else if ctx.secrets.kerberos_keytab === null}
    <p class="hint">{t('kerberos.keytab_removed')}</p>
  {/if}
  <p class="hint">{t('kerberos.keytab_hint', { spn })}</p>
</div>
