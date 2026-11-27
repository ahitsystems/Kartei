// Phone view: numbers dialled from Kartei, newest first.
// ponytail: per browser in localStorage, server-side if it should follow the user across devices
export type Call = { uid: string; v: string; label: string; at: string }
const KEY = 'kartei:phone-view-calls'

export const calls = $state<{ list: Call[] }>({ list: JSON.parse(localStorage.getItem(KEY) ?? '[]') })

/** uid '' = a number typed into the search, not from a contact */
export function logCall(uid: string, v: string, label = '') {
  calls.list = [{ uid, v, label, at: new Date().toISOString() }, ...calls.list].slice(0, 20)
  localStorage.setItem(KEY, JSON.stringify(calls.list))
}
