// L'hôte d'une adresse, jugé tel qu'il est écrit, sans DNS : la copie de `internal_host` et de `non_public` côté Python
// (`oto/connectors/custom_api/profile.py`, `oto/tools/common/url_guard.py`). Une adresse non publique est celle que
// Python dit privée, de bouclage, locale au lien, réservée, de diffusion, non spécifiée ou non globale ; une adresse IPv6
// qui porte une IPv4 (`::ffff:a.b.c.d`) est jugée comme son IPv4. L'adresse résolue reste à garder par l'hôte à chaque
// appel.

/** Les noms qui ne se résolvent qu'à l'intérieur d'un réseau. */
export const INTERNAL_SUFFIXES = [".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa", ".corp", ".private"]

/** Les blocs IPv4 non globaux : `[premier octet…, longueur du préfixe]`. */
const IPV4_NOT_GLOBAL: readonly [readonly number[], number][] = [
  [[0], 8],
  [[10], 8],
  [[100, 64], 10],
  [[127], 8],
  [[169, 254], 16],
  [[172, 16], 12],
  [[192, 0, 0], 24],
  [[192, 0, 2], 24],
  [[192, 168], 16],
  [[198, 18], 15],
  [[198, 51, 100], 24],
  [[203, 0, 113], 24],
  [[224], 4],
  [[240], 4],
]
/** Dans 192.0.0.0/24, les deux adresses globales. */
const IPV4_GLOBAL_EXCEPTIONS = ["192.0.0.9", "192.0.0.10"]

function ipv4(host: string): number[] | null {
  const parts = host.split(".")
  if (parts.length !== 4 || !parts.every((part) => /^\d{1,3}$/.test(part))) return null
  const bytes = parts.map(Number)
  return bytes.every((byte) => byte <= 255) ? bytes : null
}

function inBlock(bytes: readonly number[], prefix: readonly number[], length: number): boolean {
  const value = (b: readonly number[]) => (((b[0] ?? 0) << 24) | ((b[1] ?? 0) << 16) | ((b[2] ?? 0) << 8) | (b[3] ?? 0)) >>> 0
  const mask = length === 0 ? 0 : (0xffffffff << (32 - length)) >>> 0
  return ((value(bytes) & mask) >>> 0) === ((value(prefix) & mask) >>> 0)
}

function ipv4NotPublic(bytes: readonly number[]): boolean {
  if (IPV4_GLOBAL_EXCEPTIONS.includes(bytes.join("."))) return false
  return IPV4_NOT_GLOBAL.some(([prefix, length]) => inBlock(bytes, prefix, length))
}

/** Les huit mots d'une IPv6 écrite en hexadécimal (la forme que rend `URL`), ou `null`. */
function ipv6(host: string): number[] | null {
  if (!host.includes(":")) return null
  const [head, tail, ...more] = host.split("::")
  if (more.length) return null
  const words = (text: string | undefined) => (text ? text.split(":") : [])
  const left = words(head)
  const right = words(tail)
  const missing = 8 - left.length - right.length
  if (tail === undefined ? missing !== 0 : missing < 1) return null
  const all = [...left, ...Array(tail === undefined ? 0 : missing).fill("0"), ...right]
  if (!all.every((word) => /^[0-9a-f]{1,4}$/i.test(word))) return null
  return all.map((word) => parseInt(word, 16))
}

function ipv6NotPublic(w: readonly number[]): boolean {
  const [w0 = 0, w1 = 0, w2 = 0] = w
  if (w.slice(0, 5).every((word) => word === 0) && w[5] === 0xffff)
    return ipv4NotPublic([(w[6] ?? 0) >> 8, (w[6] ?? 0) & 0xff, (w[7] ?? 0) >> 8, (w[7] ?? 0) & 0xff])
  // Hors de 2000::/3 : réservé, privé (fc00::/7), local au lien (fe80::/10), diffusion (ff00::/8) ; fec0::/10 reste global.
  if (w0 < 0x2000 || (w0 >= 0x4000 && w0 < 0xfec0) || w0 >= 0xff00) return true
  if (w0 === 0x2001 && w1 < 0x0200) {
    const exact = (a: number) => w1 === 1 && w.slice(2, 7).every((word) => word === 0) && w[7] === a
    const global = exact(1) || exact(2) || w1 === 3 || (w1 === 4 && w2 === 0x112) || (w1 >= 0x20 && w1 <= 0x3f)
    return !global
  }
  return (w0 === 0x2001 && w1 === 0x0db8) || w0 === 0x2002 || (w0 === 0x3fff && w1 < 0x1000)
}

/** Un hôte qui n'est pas sur l'internet public, tel qu'écrit : une adresse non publique, un nom sans point, `localhost`
 * ou un suffixe gardé pour les réseaux internes. */
export function internalHost(written: string): boolean {
  const host = written.toLowerCase().replace(/\.$/, "")
  const bare = host.replace(/^\[/, "").replace(/\]$/, "")
  const v4 = ipv4(bare)
  if (v4 !== null) return ipv4NotPublic(v4)
  const v6 = ipv6(bare)
  if (v6 !== null) return ipv6NotPublic(v6)
  return !host.includes(".") || host === "localhost" || INTERNAL_SUFFIXES.some((suffix) => host.endsWith(suffix))
}

export type AddressVerdict = "template" | "not_https" | "internal" | null

/** Le défaut d'une adresse de la définition, ou `null` : fixe (sans gabarit), en https, vers un hôte public. */
export function addressVerdict(url: string): AddressVerdict {
  if (url.includes("{")) return "template"
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return /^https:/i.test(url) ? "internal" : "not_https"
  }
  if (parsed.protocol !== "https:") return "not_https"
  return !parsed.hostname || internalHost(parsed.hostname) ? "internal" : null
}
