<script lang="ts">
  import { t } from '../lib/i18n.svelte'
  import { SystemSettings } from './system/settings.svelte'
  import Carddav from './system/Carddav.svelte'
  import General from './system/General.svelte'
  import Ldap from './system/Ldap.svelte'
  import Mail from './system/Mail.svelte'
  import Outlook from './system/Outlook.svelte'
  import Review from './system/Review.svelte'
  import Sso from './system/Sso.svelte'

  let { section }: { section: 'ldap' | 'sso' | 'carddav' | 'outlook' | 'review' | 'mail' | 'general' } = $props()

  const ctx = new SystemSettings() // keyed per tab by the parent
</script>

{#if ctx.s}
  <form class="card form" onsubmit={(e) => { e.preventDefault(); ctx.save() }}>
    {#if section === 'general'}<General {ctx} />
    {:else if section === 'sso'}<Sso {ctx} />
    {:else if section === 'ldap'}<Ldap {ctx} />
    {:else if section === 'review'}<Review {ctx} />
    {:else if section === 'mail'}<Mail {ctx} />
    {:else if section === 'outlook'}<Outlook {ctx} />
    {:else}<Carddav {ctx} />{/if}
    <div class="actions"><button class="btn primary">{t('common.save')}</button></div>
  </form>
{/if}

<style>
  /* shared by the pages in system/ */
  .form { max-width: 720px; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
  .form :global(.row2) { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .form :global(.check) { display: flex; align-items: center; gap: 8px; cursor: pointer; }
  .form :global(h3) { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 6px; }
  .form :global(.mono) { font: 12px ui-monospace, monospace; }
  .form :global(.test) { display: flex; gap: 8px; }
  .form :global(.result) { padding: 10px 12px; border-radius: 8px; background: var(--ok-soft); color: var(--ok); display: grid; gap: 4px; overflow-wrap: anywhere; }
  .form :global(.result.bad) { background: var(--danger-soft); color: var(--danger); }
  .form :global(.result.info) { background: var(--panel-2); color: var(--text); }
  .form :global(.spin svg) { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .form :global(.modes) { display: flex; gap: 18px; flex-wrap: wrap; }
  .form :global(.narrow) { width: 100px; }
  .form :global(.err-line) { color: var(--danger); font-size: 12px; }
  .form :global(code) { font: 13px ui-monospace, monospace; }
  .form :global(.hint) { margin: 2px 0 0; }
  .actions { display: flex; justify-content: flex-end; }
  @media (max-width: 640px) { .form :global(.row2) { grid-template-columns: 1fr; } }
</style>
