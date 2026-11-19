// Un problème d'une définition Custom API : son code, sa place, son message. Les mêmes codes que le paquet Python
// (`oto/connectors/custom_api/problems.py`), sauf ceux de la reprise d'oto 1, qui n'existe qu'en Python.

export const KEY_NOT_ALLOWED = "custom_api_key_not_allowed"
export const AUTH_NOT_SUPPORTED = "custom_api_auth_not_supported"
export const BASE_URL_NOT_HTTPS = "custom_api_base_url_not_https"
export const BASE_URL_INTERNAL = "custom_api_base_url_internal"
export const NAME_RESERVED = "custom_api_name_reserved"
export const HEADER_AUTHENTICATES = "custom_api_header_authenticates"
export const TOO_LARGE = "custom_api_too_large"
export const EXAMPLE_MISSING = "custom_api_example_missing"
export const EXAMPLE_INVALID = "custom_api_example_invalid"
export const SCHEMA_INVALID = "custom_api_schema_invalid"
/** Une règle du format que le schéma ne sait pas dire ; `Problem.rule` la nomme quand elle a un code. */
export const RULE_BROKEN = "custom_api_rule_broken"

/** Les codes que rend `validate`. */
export const CODES = [
  KEY_NOT_ALLOWED,
  AUTH_NOT_SUPPORTED,
  BASE_URL_NOT_HTTPS,
  BASE_URL_INTERNAL,
  NAME_RESERVED,
  HEADER_AUTHENTICATES,
  TOO_LARGE,
  EXAMPLE_MISSING,
  EXAMPLE_INVALID,
  SCHEMA_INVALID,
  RULE_BROKEN,
] as const

export type ProblemCode = (typeof CODES)[number]

/**
 * `path` : à points, un indice de liste en nombre (`functions.0.examples`), vide pour le document entier. `rule` : le code
 * de la règle du format enfreinte, pour `custom_api_rule_broken` (`body_key_twice`), quand elle en a un.
 */
export type Problem = { readonly code: ProblemCode; readonly path: string; readonly message: string; readonly rule?: string }

export type PathPart = string | number

/** Un chemin tel que `Problem.path` le dit, depuis ses clés et indices. */
export function dotted(parts: readonly PathPart[]): string {
  return parts.map(String).join(".")
}

export function problem(code: ProblemCode, path: string, message: string, rule?: string): Problem {
  return rule === undefined ? { code, path, message } : { code, path, message, rule }
}

/** Les problèmes dans leur ordre, chaque `(code, path, message)` une fois. */
export function unique(problems: readonly Problem[]): Problem[] {
  const seen = new Set<string>()
  return problems.filter((p) => {
    const key = JSON.stringify([p.code, p.path, p.message])
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
