<script lang="ts">
  import { Send } from '@lucide/svelte'
  import { post } from '../../lib/api'
  import { store } from '../../lib/store.svelte'
  import { t, terr, tmsg } from '../../lib/i18n.svelte'
  import type { SystemSettings } from './settings.svelte'

  let { ctx }: { ctx: SystemSettings } = $props()
  const s = $derived(ctx.s!)
  const errors = $derived(ctx.errors)
  const save = () => ctx.save()

  // email templates: an override is stored only where it differs from the default
  let tplKind = $state<'review' | 'reminder' | 'proposals'>('review')
  let tplLang = $state<'de' | 'en'>('de')
  const tplValue = (part: 'subject' | 'body') =>
    s?.mail_templates?.[tplKind]?.[tplLang]?.[part] ?? s?.mail_template_defaults?.[tplKind]?.[tplLang]?.[part] ?? ''
  function setTpl(part: 'subject' | 'body', value: string) {
    const def = s!.mail_template_defaults[tplKind][tplLang]
    const cur = { ...def, ...(s!.mail_templates?.[tplKind]?.[tplLang] ?? {}), [part]: value }
    const all = { ...(s!.mail_templates ?? {}) }
    const langs = { ...(all[tplKind] ?? {}) }
    if (cur.subject === def.subject && cur.body === def.body) delete langs[tplLang]
    else langs[tplLang] = cur
    if (Object.keys(langs).length) all[tplKind] = langs
    else delete all[tplKind]
    s!.mail_templates = all
  }
  function resetTpl() {
    const def = s!.mail_template_defaults[tplKind][tplLang]
    setTpl('subject', def.subject)
    setTpl('body', def.body)
  }
  let mailTo = $state(store.user?.email ?? '')
  let mailResult = $state<any>(null)
  let testing = $state(false)

  async function testMail() {
    testing = true
    mailResult = null
    if (await save()) mailResult = await post('/api/admin/mail/test', { to: mailTo }).catch(() => ({ error: t('err.generic') }))
    testing = false
  }
</script>

<p class="hint">{t('mail.hint')}</p>
<div class="modes" role="radiogroup" aria-label={t('mail.transport')}>
  <label class="check"><input type="radio" bind:group={s.mail_transport} value="smtp" /> {t('mail.via_smtp')}</label>
  <label class="check"><input type="radio" bind:group={s.mail_transport} value="graph" /> {t('mail.via_graph')}</label>
</div>
{#if s.mail_transport === 'graph'}
  <p class="hint">{t('mail.graph_hint')}</p>
  <div><label class="label" for="gms">{t('mail.graph_sender')}</label>
    <input id="gms" class="input" type="email" bind:value={s.mail_graph_sender} placeholder="kartei@example.de" /></div>
{:else}
<div class="row2">
  <div><label class="label" for="sh">{t('mail.host')}</label><input id="sh" class="input" bind:value={s.smtp_host} placeholder="smtp.example.de" /></div>
  <div class="row2">
    <div><label class="label" for="sp">{t('mail.port')}</label><input id="sp" class="input" type="number" bind:value={s.smtp_port} />
      {#if errors.smtp_port}<div class="error-text">{terr(errors.smtp_port)}</div>{/if}</div>
    <div><label class="label" for="ss">{t('mail.security')}</label>
      <select id="ss" class="select" bind:value={s.smtp_security}>
        <option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">{t('mail.none')}</option>
      </select></div>
  </div>
</div>
<div class="row2">
  <div><label class="label" for="su">{t('mail.username')}</label><input id="su" class="input" bind:value={s.smtp_username} autocomplete="off" /></div>
  <div><label class="label" for="spw">{t('mail.password')}</label>
    <input id="spw" class="input" type="password" bind:value={ctx.secrets.smtp_password} placeholder={s.smtp_password_set ? '••••••••' : ''} autocomplete="new-password" /></div>
</div>
<div><label class="label" for="sf">{t('mail.from')}</label><input id="sf" class="input" bind:value={s.smtp_from} placeholder="Kartei <kartei@example.de>" /></div>
{/if}
<div class="test">
  <input class="input" type="email" bind:value={mailTo} placeholder={t('mail.test_to')} />
  <button type="button" class="btn" onclick={testMail} disabled={testing || (s.mail_transport === 'graph' ? !s.mail_graph_sender : !s.smtp_host)}><Send size={15} />{t('mail.test')}</button>
</div>
{#if mailResult}
  <div class="result" class:bad={tmsg(mailResult.error)}>
    {#if mailResult.error}<strong>{t('graph.failed')}:</strong> {tmsg(mailResult.error)}{:else}✓ {t('mail.test_ok', { to: mailResult.to })}{/if}
  </div>
{/if}
<h3>{t('mail.templates')}</h3>
<p class="hint">{t('mail.templates_hint')}</p>
<div class="row2">
  <div><label class="label" for="mtk">{t('mail.template')}</label>
    <select id="mtk" class="select" bind:value={tplKind}>
      {#each ['review', 'reminder', 'proposals'] as k}<option value={k}>{t(`mail.tpl_${k}`)}</option>{/each}
    </select></div>
  <div><label class="label" for="mtl">{t('account.language')}</label>
    <select id="mtl" class="select" bind:value={tplLang}><option value="de">Deutsch</option><option value="en">English</option></select></div>
</div>
<div><label class="label" for="mts">{t('mail.subject')}</label>
  <input id="mts" class="input" value={tplValue('subject')} oninput={(e) => setTpl('subject', e.currentTarget.value)} /></div>
<div><label class="label" for="mtb">{t('mail.body')}</label>
  <textarea id="mtb" class="input mono" rows="9" value={tplValue('body')} oninput={(e) => setTpl('body', e.currentTarget.value)}></textarea>
  {#if errors.mail_templates}<div class="error-text">{terr(errors.mail_templates)}</div>{/if}</div>
<div class="test">
  <span class="hint">{t('mail.placeholders')}</span>
  <button type="button" class="btn sm" onclick={resetTpl} disabled={!s.mail_templates?.[tplKind]?.[tplLang]}>{t('mail.reset')}</button>
</div>
