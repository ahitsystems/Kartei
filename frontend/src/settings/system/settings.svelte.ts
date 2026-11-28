import { ApiError, get, put } from '../../lib/api'
import { store } from '../../lib/store.svelte'
import { t } from '../../lib/i18n.svelte'
import { toast } from '../../lib/toast.svelte'

const noSecrets = () => ({ ldap_bind_password: '', graph_client_secret: '', smtp_password: '', oidc_client_secret: '', kerberos_keytab: '' as string | null })

/** The settings behind the pages of System.svelte: loaded once, saved as a whole by every page. */
export class SystemSettings {
  s = $state<Record<string, any> | null>(null)
  errors = $state<Record<string, string>>({})
  /** never returned by the API: sent with the next save if filled in (null: remove the stored one), then cleared */
  secrets = $state(noSecrets())

  constructor() {
    get<Record<string, any>>('/api/admin/settings').then((r) => (this.s = r))
  }

  async save() {
    this.errors = {}
    const body = { ...$state.snapshot(this.s) } as Record<string, any>
    for (const [k, v] of Object.entries(this.secrets)) if (v !== '') body[k] = v ?? ''
    try {
      this.s = await put('/api/admin/settings', body)
      this.secrets = noSecrets()
      toast(t('settings.saved'))
      store.sync()
      return true
    } catch (e) {
      if (e instanceof ApiError && e.code === 'validation') this.errors = e.fields
      else toast(t('err.generic'), { kind: 'error' })
      return false
    }
  }
}
