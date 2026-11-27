<script lang="ts">
  import Fields from './Fields.svelte'
  import Users from './Users.svelte'
  import System from './System.svelte'
  import Exports from './Exports.svelte'
  import Import from './Import.svelte'
  import Audit from './Audit.svelte'
  import Tokens from './Tokens.svelte'
  import Webhooks from './Webhooks.svelte'
  import ActiveDirectory from './ActiveDirectory.svelte'
  import Monitoring from './Monitoring.svelte'
  import Backup from './Backup.svelte'
  import Phonebook from './Phonebook.svelte'
  import { store } from '../lib/store.svelte'
  import { t } from '../lib/i18n.svelte'

  import { go } from '../lib/router.svelte'
  import { Activity, Braces, ClipboardCheck, DatabaseBackup, FileDown, FileUp, FolderSync, KeyRound, LogIn, Mail, Network, Phone,
           ScrollText, SlidersHorizontal, TextCursorInput, Users as UsersIcon } from '@lucide/svelte'

  let { tab }: { tab: string } = $props()
  // grouped by what an admin is doing; the order inside a group follows the setup order of the admin manual
  const groups = [
    { key: 'basics', items: [['general', SlidersHorizontal], ['fields', TextCursorInput], ['users', UsersIcon], ['review', ClipboardCheck], ['mail', Mail]] },
    { key: 'signin', items: [['ldap', KeyRound], ['sso', LogIn]] },
    { key: 'distribution', items: [['outlook', FolderSync], ['carddav', Phone], ['ad', Network], ['exports', FileDown]] },
    { key: 'data', items: [['import', FileUp], ['api', Braces]] },
    { key: 'operations', items: [['backup', DatabaseBackup], ['monitoring', Activity], ['audit', ScrollText]] },
  ] as const
</script>

{#if !store.isAdmin}
  <div class="page"><p class="muted">{t('err.forbidden')}</p></div>
{:else}
  <div class="page">
    <nav class="side" aria-label={t('top.settings')}>
      <h1>{t('top.settings')}</h1>
      {#each groups as g (g.key)}
        <h2>{t(`settings.group.${g.key}`)}</h2>
        {#each g.items as [name, Icon] (name)}
          <a href="/settings/{name}" class:on={tab === name} aria-current={tab === name ? 'page' : undefined}><Icon size={15} />{t(`settings.${name}`)}</a>
        {/each}
      {/each}
    </nav>
    <!-- phones: the same list as a select -->
    <select class="select pick" value={tab} aria-label={t('top.settings')} onchange={(e) => go(`/settings/${e.currentTarget.value}`)}>
      {#each groups as g (g.key)}
        <optgroup label={t(`settings.group.${g.key}`)}>
          {#each g.items as [name] (name)}<option value={name}>{t(`settings.${name}`)}</option>{/each}
        </optgroup>
      {/each}
    </select>
    <div class="content">
    {#if tab === 'fields'}<Fields />
    {:else if tab === 'users'}<Users />
    {:else if ['ldap', 'sso', 'carddav', 'outlook', 'review', 'mail', 'general'].includes(tab)}{#key tab}<System section={tab as any} />{/key}{#if tab === 'carddav'}<Phonebook />{/if}
    {:else if tab === 'exports'}<Exports />
    {:else if tab === 'import'}<Import />
    {:else if tab === 'audit'}<Audit />
    {:else if tab === 'api'}<Tokens /><Webhooks />
    {:else if tab === 'ad'}<ActiveDirectory />
    {:else if tab === 'monitoring'}<Monitoring />
    {:else if tab === 'backup'}<Backup />
    {/if}
    </div>
  </div>
{/if}

<style>
  .page { max-width: 1440px; margin: 0 auto; padding: 24px 28px 60px; display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 32px; align-items: start; }
  /* sticks while the page scrolls; taller than the window: scrolls on its own */
  .side { position: sticky; top: 0; max-height: calc(100dvh - 52px - 24px); overflow-y: auto; display: flex; flex-direction: column; gap: 1px; }
  h1 { font-size: 22px; margin-bottom: 10px; }
  h2 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--faint); margin: 12px 10px 3px; }
  .side a { display: flex; align-items: center; gap: 9px; padding: 5px 10px; border-radius: 7px; color: var(--muted); font-weight: 500; }
  .side a:hover { color: var(--text); background: var(--panel-2); text-decoration: none; }
  .side a.on { color: var(--accent-text); background: var(--accent-soft); }
  .pick { display: none; }
  @media (max-width: 860px) {
    .page { padding: 14px 16px 60px; grid-template-columns: minmax(0, 1fr); gap: 14px; }
    .side { display: none; }
    .pick { display: block; }
  }
</style>
