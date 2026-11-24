// Les règles du format que le schéma JSON ne sait pas dire et dont le profil Custom API a besoin : la copie TypeScript de
// `oto/connectors/rules/` (corps, règles 20, 22 et 23 ; arguments 1, authentification 2, un nom côté API une valeur 3,
// contrôles 4, sonde 5, `act` 9, signal de refus 10, délai 11, `personal` 15, `strip` 18). Chacune lève
// `DescriptionError` au premier défaut, sur une description déjà conforme au schéma, avec le message du côté Python ; les
// cas communs `connectors/_cases/custom_api.yaml` tiennent les deux copies ensemble.

// Un document JSON, lu champ à champ comme le lit la copie Python, sur une description que le schéma a acceptée.
type Json = any

export class DescriptionError extends Error {}

/** Le délai d'un appel quand le connecteur n'en déclare pas (le `default` du schéma). */
export const DEFAULT_TIMEOUT_S = 30
const AUTH_REFERENCES = ["key", "token", "username", "password", "client_id", "client_secret"]
const PLACES = ["query", "body", "headers", "files"]
/** Les méthodes qui n'envoient pas de corps. */
const NO_BODY_METHODS = ["GET"]

export const isObject = (value: unknown): value is { [key: string]: Json } =>
  value !== null && typeof value === "object" && !Array.isArray(value)

/** La vérité d'une valeur au sens de Python : vides, zéro, `null` et `false` sont faux. */
export function truthy(value: unknown): boolean {
  if (Array.isArray(value) || typeof value === "string") return value.length > 0
  if (isObject(value)) return Object.keys(value).length > 0
  return Boolean(value)
}

/** Le texte d'une valeur comme Python l'écrit avec `!r` dans un message. */
export function repr(value: unknown): string {
  if (typeof value !== "string") return JSON.stringify(value)
  return value.includes("'") && !value.includes('"') ? `"${value}"` : `'${value.replaceAll("'", "\\'")}'`
}

/** Les paramètres d'un chemin (`/items/{id}` → `id`), dans l'ordre. */
export function pathParams(path: string): string[] {
  return [...path.matchAll(/\{([^{}]*)\}/g)].map((match) => match[1] ?? "")
}

function bodyArgument(call: Json): string | null {
  return typeof call.body === "string" ? call.body : null
}

/** Les entrées d'un corps en table, à toute profondeur : le chemin des clés côté API et l'argument. */
function bodyEntries(table: Json, at: string[] = []): [string[], string][] {
  const entries: [string[], string][] = []
  for (const [key, value] of Object.entries(table)) {
    if (isObject(value)) entries.push(...bodyEntries(value, [...at, key]))
    else entries.push([[...at, key], value as string])
  }
  return entries
}

/** La table d'une place de `call` : nom côté API → argument ; celle du corps mise à plat, ses clés jointes d'un point. */
function argumentTable(call: Json, kind: string): { [apiName: string]: string } {
  const table = call[kind]
  if (!isObject(table)) return {}
  if (kind === "body") return Object.fromEntries(bodyEntries(table).map(([path, argument]) => [path.join("."), argument]))
  return table
}

export const BODY_REFUSALS = [
  "body_argument_unknown",
  "body_argument_not_strict",
  "body_argument_optional",
  "body_argument_method",
  "body_argument_elsewhere",
  "body_argument_encoded",
  "body_argument_not_alone",
  "body_argument_paginated",
] as const
export const NESTED_BODY_REFUSALS = ["body_nested_empty", "body_key_twice", "body_nested_multipart"] as const
export const LIST_BODY_REFUSALS = ["body_list_without_body", "body_list_method", "body_list_multipart"] as const
export const STRIP_REFUSALS = ["strip_path_malformed", "strip_path_overlaps", "strip_path_twice"] as const

const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** Des clés libres toutes donnée personnelle : exactement `{type: string, personal: true}` ou la valeur simple. */
function freeKeys(additional: unknown): boolean {
  return (
    isObject(additional) &&
    sameJson(Object.keys(additional).sort(), ["personal", "type"]) &&
    additional.personal === true &&
    (additional.type === "string" || sameJson(additional.type, ["string", "number", "boolean"]))
  )
}

function strictObject(schema: unknown): boolean {
  if (!isObject(schema)) return false
  const additional = schema.additionalProperties
  return (
    schema.type === "object" &&
    (additional === false || freeKeys(additional)) &&
    isObject(schema.properties) &&
    Object.keys(schema.properties).length >= 1 &&
    !("patternProperties" in schema)
  )
}

function bodyArgumentRefusal(fn: Json): [string, string] | null {
  const call = fn.call
  const argument = bodyArgument(call)
  if (argument === null) return null
  const properties = fn.input.properties
  if (!(argument in properties)) return ["body_argument_unknown", "is not an input argument"]
  if (!strictObject(properties[argument]))
    return [
      "body_argument_not_strict",
      "must be a strict object (type: object, additionalProperties: false or free keys " +
        "{type: string, personal: true} or {type: [string, number, boolean], personal: true}, at least one " +
        "declared property, no patternProperties): the body is what the agent may send, never a free bag",
    ]
  if (!(fn.input.required ?? []).includes(argument))
    return ["body_argument_optional", "must be required: the whole body is never absent"]
  if (NO_BODY_METHODS.includes(call.method))
    return ["body_argument_method", `needs a method that sends a body, not ${call.method}`]
  const elsewhere = pathParams(call.path)
  for (const kind of ["query", "headers", "files"]) elsewhere.push(...Object.values(argumentTable(call, kind)))
  if (elsewhere.includes(argument))
    return ["body_argument_elsewhere", "is the whole body, so it goes nowhere else (path, query, headers, files)"]
  if (argument in (call.encode || {})) return ["body_argument_encoded", "is sent as the JSON object it is, never encoded"]
  if ("body" in (call.constants || {}) || "files" in call)
    return ["body_argument_not_alone", "is the whole body: no body constant nor file part joins it"]
  if ("pagination" in fn) return ["body_argument_paginated", "a function whose body is one argument is not paginated"]
  return null
}

function emptyTable(table: Json, at: string[]): string[] | null {
  for (const [key, value] of Object.entries(table)) {
    if (!isObject(value)) continue
    const found = Object.keys(value).length === 0 ? [...at, key] : emptyTable(value, [...at, key])
    if (found !== null) return found
  }
  return null
}

function clash(table: Json, constant: unknown, at: string[]): string[] | null {
  if (!isObject(constant)) return null
  for (const [key, value] of Object.entries(table)) {
    if (!(key in constant)) continue
    if (!(isObject(value) && isObject(constant[key]))) return [...at, key]
    const found = clash(value, constant[key], [...at, key])
    if (found !== null) return found
  }
  return null
}

function bodyTableRefusal(fn: Json): [string, string, string] | null {
  const call = fn.call
  const table = call.body
  if (!isObject(table)) return null
  const empty = emptyTable(table, [])
  if (empty !== null) return ["body_nested_empty", empty.join("."), "is a nested table without any entry: it places nothing"]
  const met = clash(table, (call.constants || {}).body, [])
  if (met !== null)
    return [
      "body_key_twice",
      met.join("."),
      "is both a body constant and an argument, or one is under the other: they meet only through objects",
    ]
  if ("files" in call) {
    const nested = bodyEntries(table).find(([path]) => path.length > 1)
    if (nested !== undefined)
      return [
        "body_nested_multipart",
        nested[0].join("."),
        "is nested in a multipart body, whose parts are texts: a file part rules out a nested table",
      ]
  }
  return null
}

function bodyListRefusal(fn: Json): [string, string] | null {
  const call = fn.call
  if (call.body_list !== true) return null
  if (!(truthy(call.body) || "body" in (call.constants || {})))
    return ["body_list_without_body", "sends the body as a list, and the function has no body to put in it"]
  if (NO_BODY_METHODS.includes(call.method)) return ["body_list_method", `needs a method that sends a body, not ${call.method}`]
  if ("files" in call) return ["body_list_multipart", "a multipart body (files) is a form, never a JSON list"]
  return null
}

/** Règle 1 : un argument va à un seul endroit, et tout endroit nomme un argument. */
export function checkArguments(fn: Json, where: string): void {
  const call = fn.call
  if (call === undefined) return
  const name = fn.name
  const declared = new Set(Object.keys(fn.input.properties))
  const required = new Set<string>(fn.input.required ?? [])
  const placed = new Map<string, string>()
  const place = (argument: string, at: string) => {
    if (!declared.has(argument))
      throw new DescriptionError(`${where}: function ${name}: ${at} names ${repr(argument)}, which is not an input argument`)
    if (placed.has(argument))
      throw new DescriptionError(
        `${where}: function ${name}: argument ${repr(argument)} goes to two places (${placed.get(argument)} and ${at})`,
      )
    placed.set(argument, at)
  }
  const whole = bodyArgument(call)
  const refusal = bodyArgumentRefusal(fn)
  if (refusal !== null) throw new DescriptionError(`${where}: function ${name}: call.body ${repr(whole)}: ${refusal[0]}: ${refusal[1]}`)
  if (whole !== null) place(whole, "the whole body")
  const nested = bodyTableRefusal(fn)
  if (nested !== null)
    throw new DescriptionError(`${where}: function ${name}: call.body ${repr(nested[1])}: ${nested[0]}: ${nested[2]}`)
  const listed = bodyListRefusal(fn)
  if (listed !== null) throw new DescriptionError(`${where}: function ${name}: call.body_list: ${listed[0]}: ${listed[1]}`)
  for (const argument of pathParams(call.path)) {
    place(argument, "the path")
    if (!required.has(argument))
      throw new DescriptionError(`${where}: function ${name}: path argument ${repr(argument)} must be required`)
  }
  for (const kind of PLACES)
    for (const [apiName, argument] of Object.entries(argumentTable(call, kind))) place(argument, `${kind} ${repr(apiName)}`)
  const pagination = fn.pagination
  const unplaced = [...declared].filter((argument) => !placed.has(argument)).sort()
  if (unplaced.length) throw new DescriptionError(`${where}: function ${name}: argument(s) ${unplaced.join(", ")} go nowhere in call`)
  if (truthy(pagination) && !declared.has(pagination.request_param))
    throw new DescriptionError(
      `${where}: function ${name}: pagination request_param ${repr(pagination.request_param)} is not an input argument`,
    )
  for (const argument of Object.keys(call.encode || {})) {
    const at = placed.get(argument)
    if (at === undefined || at === "the path" || at.startsWith("files "))
      throw new DescriptionError(
        `${where}: function ${name}: encode names ${repr(argument)}, which is not placed in query, body or headers` +
          " (a file is never encoded)",
      )
  }
}

/**
 * Règle 2 : une référence de `auth` désigne un champ de `credential` existant ; en `oauth2_jwt_bearer`, le seul, secret.
 * En `oauth2_user`, l'indicateur de ressource ne se déclare que par `resource`, jamais dans `authorize_params`.
 */
export function checkAuth(connector: Json, where: string): void {
  const auth = connector.auth
  if (auth.authorize_params !== undefined && "resource" in auth.authorize_params)
    throw new DescriptionError(
      `${where}: auth.authorize_params.resource: declare the resource indicator (RFC 8707) as auth.resource, ` +
        "which the host sets at the authorization, the code exchange and each renewal; remove it from authorize_params",
    )
  const credential: Json[] = connector.credential ?? []
  const fields = new Set(credential.map((field) => field.name as string))
  for (const reference of AUTH_REFERENCES) {
    if (reference in auth && !fields.has(auth[reference]))
      throw new DescriptionError(
        `${where}: auth.${reference} names ${repr(auth[reference])}, which is not a credential field ` +
          `(${[...fields].sort().join(", ") || "none declared"})`,
      )
  }
  if (auth.kind === "oauth2_jwt_bearer") {
    const others = [...fields].filter((field) => field !== auth.key).sort()
    if (others.length)
      throw new DescriptionError(
        `${where}: auth.kind oauth2_jwt_bearer takes a single credential field, the key file named by auth.key ` +
          `(${repr(auth.key)}); remove ${others.join(", ")}`,
      )
    const field = credential.find((f) => f.name === auth.key)
    if (field.secret !== true)
      throw new DescriptionError(`${where}: auth.key names ${repr(auth.key)}, which must be a secret field (secret: true)`)
  }
}

/** L'en-tête que pose l'authentification, ou `null`. */
export function authHeader(auth: Json): string | null {
  if (auth.kind === "api_key") return auth.in === "header" ? auth.name : null
  if (["bearer", "basic", "oauth2_client_credentials", "oauth2_user", "oauth2_jwt_bearer", "host_delegated"].includes(auth.kind))
    return "Authorization"
  return null
}

const authQuery = (auth: Json): string | null => (auth.kind === "api_key" && auth.in === "query" ? auth.name : null)

/** Règle 3 : un nom côté API, une seule valeur. */
export function checkConstants(connector: Json, fn: Json, where: string): void {
  const call = fn.call
  if (call === undefined) return
  const name = fn.name
  const constants = call.constants || {}
  const query = argumentTable(call, "query")
  for (const apiName of Object.keys(constants.query || {}))
    if (apiName in query) throw new DescriptionError(`${where}: function ${name}: query ${repr(apiName)} is both a constant and an argument`)
  const inQuery = authQuery(connector.auth)
  if (inQuery && (inQuery in (call.query || {}) || inQuery in (constants.query || {})))
    throw new DescriptionError(`${where}: function ${name}: query ${repr(inQuery)} is set twice (the authentication and the function)`)
  const seen = new Map<string, string>()
  const header = authHeader(connector.auth)
  if (header) seen.set(header.toLowerCase(), "the authentication")
  const sources: [string, Json][] = [
    ["the connector headers", connector.headers || {}],
    ["the function constant headers", constants.headers || {}],
    ["the function headers", call.headers || {}],
  ]
  for (const [source, headers] of sources)
    for (const key of Object.keys(headers)) {
      const low = key.toLowerCase()
      if (seen.has(low))
        throw new DescriptionError(`${where}: function ${name}: header ${repr(key)} is set twice (${seen.get(low)} and ${source})`)
      seen.set(low, source)
    }
}

/** Ce qui engage ou détruit : `act` déclaré, sinon déduit de la classe. */
export function act(fn: Json): boolean {
  return "act" in fn ? fn.act : fn.class !== "read"
}

/** Règle 9 : `act` ne contredit pas la classe, et `effect` n'est dit que d'une fonction sensible. */
export function checkAct(fn: Json, where: string): void {
  if (fn.act === false && fn.class === "sensitive")
    throw new DescriptionError(`${where}: function ${fn.name}: a sensitive function cannot declare act: false`)
  if (fn.act === true && fn.class === "read")
    throw new DescriptionError(`${where}: function ${fn.name}: a read function cannot declare act: true`)
  if ("effect" in fn && fn.class !== "sensitive")
    throw new DescriptionError(`${where}: function ${fn.name}: effect is said of a sensitive function only`)
}

/** Règle 11 : le délai d'une fonction n'est pas celui du connecteur. */
export function checkTimeout(connector: Json, fn: Json, where: string): void {
  const own = fn.timeout_s
  if (own !== undefined && own !== null && own === (connector.timeout_s ?? DEFAULT_TIMEOUT_S))
    throw new DescriptionError(`${where}: function ${fn.name}: timeout_s ${own} is the connector's timeout: declare none`)
}

/** Règle 18 : le premier défaut d'une liste `strip`, `[code, chemin, raison]`, ou `null`. */
export function stripRefusal(paths: readonly string[]): [string, string, string] | null {
  const seen = new Set<string>()
  for (const path of paths) {
    if (path.split(".").some((key) => key === ""))
      return ["strip_path_malformed", path, "a path is keys separated by dots, each non-empty (no leading, trailing or doubled dot)"]
    if (seen.has(path)) return ["strip_path_twice", path, "declared twice"]
    seen.add(path)
  }
  for (const path of paths) {
    const above = paths.find((other) => path.startsWith(`${other}.`))
    if (above !== undefined) return ["strip_path_overlaps", path, `lies under ${repr(above)}, which is already removed whole`]
  }
  return null
}

const SCHEMA_KEYWORDS = [
  "items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames", "unevaluatedItems",
  "unevaluatedProperties",
]
const SCHEMA_LISTS = ["allOf", "anyOf", "oneOf", "prefixItems"]
const SCHEMA_MAPS = ["properties", "patternProperties", "$defs", "dependentSchemas"]

function personalMarks(schema: unknown, at: string): [string, unknown][] {
  if (!isObject(schema)) return []
  const found: [string, unknown][] = "personal" in schema ? [[at, schema.personal]] : []
  for (const key of SCHEMA_KEYWORDS) found.push(...personalMarks(schema[key], `${at}.${key}`))
  for (const key of SCHEMA_LISTS)
    (schema[key] || []).forEach((child: unknown, index: number) => found.push(...personalMarks(child, `${at}.${key}[${index}]`)))
  for (const key of SCHEMA_MAPS)
    for (const [name, child] of Object.entries(schema[key] || {})) found.push(...personalMarks(child, `${at}.${key}.${name}`))
  return found
}

/** Règle 15 : `personal` marque une propriété de l'entrée, toujours `true` ; ni la racine ni la sortie. */
export function checkPersonal(fn: Json, where: string): void {
  for (const [at, value] of personalMarks(fn.input, "input")) {
    if (at === "input") throw new DescriptionError(`${where}: function ${fn.name}: personal marks a property of input, not input itself`)
    if (value !== true)
      throw new DescriptionError(`${where}: function ${fn.name}: ${at}: personal must be true (declare it only where it applies)`)
  }
  for (const [at] of personalMarks((fn.output || {}).schema, "output.schema"))
    throw new DescriptionError(`${where}: function ${fn.name}: ${at}: personal marks an input argument, not an output field`)
}

function statuses(entry: Json): number[] {
  const status = "status" in entry ? entry.status : entry.when
  return Array.isArray(status) ? status : [status]
}

function checkSignalTable(entries: Json[], at: string, where: string): void {
  const plain = new Set<number>()
  for (const entry of entries) {
    const label = `${at} ${repr(entry.code)}`
    if (!("status" in entry) && !Number.isInteger(entry.when)) {
      if ("body" in entry || "marks_account" in entry)
        throw new DescriptionError(
          `${where}: ${label}: a refusal with body or marks_account needs an HTTP status in when, not a text condition`,
        )
      continue
    }
    const codes = statuses(entry)
    const body = "body" in entry
    const mark = entry.marks_account
    if (!body && codes.some((status) => status >= 200 && status < 300))
      throw new DescriptionError(`${where}: ${label}: a 2xx status needs a body condition, or every success would be refused`)
    if (mark === "credential_rejected" && !body && codes.includes(403))
      throw new DescriptionError(
        `${where}: ${label}: a 403 without a body condition cannot mark credential_rejected (a missing right is not a refused key)`,
      )
    if (mark === "quota_exhausted" && !body && codes.includes(429))
      throw new DescriptionError(
        `${where}: ${label}: a 429 without a body condition cannot mark quota_exhausted (a rate limit is not spent credits)`,
      )
    if (mark !== undefined && mark !== null && entry.retryable === true)
      throw new DescriptionError(`${where}: ${label}: an entry that marks the account is not retryable`)
    const shadowed = codes.filter((status) => plain.has(status)).sort((a, b) => a - b)
    if (body && shadowed.length)
      throw new DescriptionError(
        `${where}: ${label}: its body condition comes after an entry without one for status ` +
          `${[...new Set(shadowed)].join(", ")}, so it is never read`,
      )
    if (!body) for (const status of codes) plain.add(status)
  }
}

/** Règle 10 : le signal de refus du service, dans la table du connecteur et dans les refus de chaque fonction. */
export function checkSignals(data: Json, where: string): void {
  checkSignalTable(data.connector.errors, "errors", where)
  for (const fn of data.functions) checkSignalTable(fn.refusals ?? [], `function ${fn.name}: refusal`, where)
}

/** Règle 4 : un contrôle nomme un refus de sa fonction, et `equal_sums` une liste qui a ses deux champs. */
export function checkChecks(fn: Json, where: string): void {
  const refusals = new Set((fn.refusals ?? []).map((refusal: Json) => refusal.code))
  for (const check of [...(fn.checks ?? []), ...(fn.expect ?? [])]) {
    if (!refusals.has(check.refusal))
      throw new DescriptionError(
        `${where}: function ${fn.name}: ${check.kind} names refusal ${repr(check.refusal)}, which the function does not declare`,
      )
    if (check.kind === "equal_sums") {
      const argument = fn.input.properties[check.items] ?? {}
      const item = argument.type === "array" ? argument.items : undefined
      const properties = (item ?? {}).properties ?? {}
      const missing = check.fields.filter((field: string) => !(field in properties))
      if (!item || missing.length)
        throw new DescriptionError(
          `${where}: function ${fn.name}: equal_sums needs ${repr(check.items)} to be a list argument whose items ` +
            `declare ${check.fields.join(", ")}`,
        )
    }
  }
}

function plainRead(data: Json, role: string, name: string, where: string): void {
  const fn = data.functions.find((f: Json) => f.name === name)
  if (fn === undefined) throw new DescriptionError(`${where}: ${role} names ${repr(name)}, which is not a function of the connector`)
  if (fn.class !== "read" || !("call" in fn) || truthy(fn.input.required))
    throw new DescriptionError(`${where}: ${role} ${repr(name)} must be a read function with a call and no required argument`)
}

/** Règle 5 : la sonde et l'identité d'un consentement sont des lectures par `call`, appelables sans argument. */
export function checkProbe(data: Json, where: string): void {
  const probe = data.connector.probe
  if (probe !== undefined) {
    plainRead(data, "probe", probe.function, where)
    const fn = data.functions.find((f: Json) => f.name === probe.function)
    if ("balance" in probe && "cost" in fn)
      throw new DescriptionError(
        `${where}: probe ${repr(probe.function)} reads a balance, so it must declare no cost: one never pays to learn whether one can pay`,
      )
  }
  const auth = data.connector.auth
  const identity = auth.identity
  if (identity !== undefined && "function" in identity) plainRead(data, "auth.identity", identity.function, where)
  if (identity !== undefined && "url" in identity && !("access_levels" in auth)) {
    const asked = new Set(auth.scopes ?? [])
    const beyond = [...new Set<string>(identity.scopes)].filter((scope) => !asked.has(scope)).sort()
    if (beyond.length) throw new DescriptionError(`${where}: auth.identity needs ${beyond.join(", ")}, which auth.scopes does not ask for`)
  }
}
