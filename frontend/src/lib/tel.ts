export interface TelPrefix { outside: string; plus: string }

/** tel: link for a stored number, dialled through a phone system: international numbers get the outside-line prefix
 *  and "+" replaced (e.g. 0 and 00: +49 511 1234 -> 000495111234), national ones (leading 0) the outside-line prefix,
 *  extensions stay as they are. Without settings the number is only stripped to digits and "+". */
export function telHref(v: unknown, p: TelPrefix = { outside: '', plus: '' }): string {
  const n = String(v ?? '').replace(/[^\d+*#]/g, '')
  if (n.startsWith('+')) return `tel:${p.outside}${p.plus ? p.plus + n.slice(1) : n}`
  if (n.startsWith('0')) return `tel:${p.outside}${n}`
  return `tel:${n}`
}
