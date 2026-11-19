// Le profil Custom API, copie TypeScript de `oto/connectors/custom_api/profile.py` (docs/conception/custom-api.md) : une
// définition EST un `connector.yaml` du format, restreint à un appel REST JSON simple et à ce qui ne touche jamais aux
// ressources de l'hôte. `validate(document, compile)` rend tous les problèmes, chacun avec son code `custom_api_*`, dans
// l'ordre des juges : le profil, puis le schéma du format, puis les exemples et les règles du format. Une liste vide : la
// définition peut s'enregistrer.
//
// Le paquet reste sans dépendance : le validateur JSON Schema (2020-12) arrive en paramètre, `compile`, qui rend pour un
// schéma la fonction qui liste les erreurs d'une valeur dans la forme d'Ajv. Il ne valide pas `format` (une annotation,
// comme côté Python) et connaît le mot `personal` (un booléen), que portent les entrées.

import type { JsonSchema } from "../types"
import { connectorSchema } from "../connector-schema"
import { addressVerdict } from "./address"
import {
  AUTH_NOT_SUPPORTED,
  BASE_URL_INTERNAL,
  BASE_URL_NOT_HTTPS,
  EXAMPLE_INVALID,
  EXAMPLE_MISSING,
  HEADER_AUTHENTICATES,
  KEY_NOT_ALLOWED,
  NAME_RESERVED,
  RULE_BROKEN,
  SCHEMA_INVALID,
  TOO_LARGE,
  dotted,
  problem,
  unique,
  type PathPart,
  type Problem,
} from "./problems"
import {
  BODY_REFUSALS,
  DescriptionError,
  LIST_BODY_REFUSALS,
  NESTED_BODY_REFUSALS,
  authHeader,
  checkAct,
  checkArguments,
  checkAuth,
  checkChecks,
  checkConstants,
  checkPersonal,
  checkProbe,
  checkSignals,
  checkTimeout,
  isObject,
  repr,
  stripRefusal,
} from "./rules"

/** Une erreur de validateur JSON Schema, dans la forme d'Ajv : la place de la valeur (pointeur JSON), le mot du schéma
 * qui la refuse, et pour `required` et `additionalProperties`, la clé manquante ou en trop. */
export type SchemaIssue = {
  readonly instancePath: string
  readonly keyword: string
  readonly message?: string
  readonly params?: { readonly missingProperty?: string; readonly additionalProperty?: string; readonly [key: string]: unknown }
}

/** Compile un schéma JSON (2020-12) et rend la fonction qui liste les erreurs d'une valeur, `[]` quand elle est valide. */
export type SchemaCompiler = (schema: JsonSchema) => (value: unknown) => readonly SchemaIssue[]

/** Le préfixe réservé du nom et de l'espace de noms d'une Custom API ; aucun connecteur partagé ne le porte. */
export const PREFIX = "custom_"
export const MAX_FUNCTIONS = 100
export const MAX_BYTES = 256 * 1024

const TOP_KEYS = ["connector", "functions"]
const CONNECTOR_KEYS = [
  "name", "label", "namespace", "version", "api_version", "base_url", "auth", "credential", "modes", "timeout_s",
  "query_arrays", "headers", "rate_limit", "probe", "errors",
]
const FUNCTION_KEYS = [
  "name", "class", "act", "effect", "description", "input", "examples", "refusals", "checks", "expect", "call",
  "pagination", "output", "timeout_s", "confirm",
]
export const AUTH_KINDS = ["none", "api_key", "bearer", "basic", "oauth2_client_credentials", "oauth2_user"]
const AUTH_REFUSED = ["token_urls", "access_levels", "scope_implies", "default_level", "from_token"]
const MODES = ["byo_user", "byo_org"]
const FORBIDDEN_HEADERS = ["authorization", "cookie", "set-cookie", "host", "proxy-authorization", "x-api-key", "api-key"]
const CODED_RULES: readonly string[] = [...BODY_REFUSALS, ...NESTED_BODY_REFUSALS, ...LIST_BODY_REFUSALS]
const WHERE = "definition"
const FOREIGN = "it touches the resources of the host or goes beyond simple REST"

type Json = any

function foreignKeys(value: unknown, admitted: readonly string[], at: PathPart[], why: string): Problem[] {
  if (!isObject(value)) return []
  return Object.keys(value)
    .filter((key) => !admitted.includes(key))
    .map((key) => problem(KEY_NOT_ALLOWED, dotted([...at, key]), `${repr(key)} is not admitted in a Custom API definition: ${why}`))
}

function refusedKeys(value: unknown, refused: readonly string[], at: PathPart[], why: string): Problem[] {
  if (!isObject(value)) return []
  return refused
    .filter((key) => key in value)
    .map((key) => problem(KEY_NOT_ALLOWED, dotted([...at, key]), `${repr(key)} is not admitted in a Custom API definition: ${why}`))
}

function address(url: unknown, at: PathPart[]): Problem[] {
  if (typeof url !== "string") return []
  const path = dotted(at)
  switch (addressVerdict(url)) {
    case "template":
      return [problem(KEY_NOT_ALLOWED, path, "a Custom API address is fixed: a template cites settings, which a definition has not")]
    case "not_https":
      return [problem(BASE_URL_NOT_HTTPS, path, "the address must start with https://")]
    case "internal":
      return [
        problem(
          BASE_URL_INTERNAL,
          path,
          "the address must name a host on the public internet, never a private, loopback or internal one",
        ),
      ]
    case null:
      return []
  }
}

function authHeaderName(auth: unknown): string | null {
  if (!isObject(auth)) return null
  const header = authHeader(auth)
  return typeof header === "string" ? header : null
}

function headers(value: unknown, at: PathPart[], auth: string | null): Problem[] {
  if (!isObject(value)) return []
  return Object.keys(value)
    .filter((name) => {
      const low = name.toLowerCase()
      return FORBIDDEN_HEADERS.includes(low) || low.startsWith("proxy-") || (auth !== null && low === auth.toLowerCase())
    })
    .map((name) =>
      problem(
        HEADER_AUTHENTICATES,
        dotted([...at, name]),
        `the header ${repr(name)} authenticates or routes the request: the secret lives in the vault, never in a definition`,
      ),
    )
}

function connectorProblems(connector: unknown): Problem[] {
  if (!isObject(connector)) return []
  const at = ["connector"]
  const out = foreignKeys(connector, CONNECTOR_KEYS, at, FOREIGN)
  for (const key of ["name", "namespace"]) {
    const value = connector[key]
    if (typeof value === "string" && !value.startsWith(PREFIX))
      out.push(problem(NAME_RESERVED, dotted([...at, key]), `a Custom API's ${key} starts with the reserved prefix ${repr(PREFIX)}`))
  }
  const auth = connector.auth
  if (isObject(auth)) {
    const kind = auth.kind
    if (typeof kind === "string" && !AUTH_KINDS.includes(kind))
      out.push(
        problem(
          AUTH_NOT_SUPPORTED,
          dotted([...at, "auth", "kind"]),
          `${repr(kind)} is not a Custom API authentication (${AUTH_KINDS.join(", ")})`,
        ),
      )
    out.push(...refusedKeys(auth, AUTH_REFUSED, [...at, "auth"], "a definition has neither settings nor access levels"))
    for (const key of ["token_url", "authorize_url"]) out.push(...address(auth[key], [...at, "auth", key]))
    if (isObject(auth.identity)) out.push(...address(auth.identity.url, [...at, "auth", "identity", "url"]))
  }
  out.push(...address(connector.base_url, [...at, "base_url"]))
  if (Array.isArray(connector.modes))
    connector.modes.forEach((mode: unknown, i: number) => {
      if (!MODES.includes(mode as string))
        out.push(
          problem(
            KEY_NOT_ALLOWED,
            dotted([...at, "modes", i]),
            `the mode ${repr(mode)} is not admitted: a definition is held by a person or an org (${MODES.join(", ")})`,
          ),
        )
    })
  if (Array.isArray(connector.credential))
    connector.credential.forEach((field: unknown, i: number) => {
      if (isObject(field) && (field.holder ?? "account") !== "account")
        out.push(
          problem(
            KEY_NOT_ALLOWED,
            dotted([...at, "credential", i, "holder"]),
            "a field held by the host is not admitted: every field of a definition comes with the account",
          ),
        )
    })
  out.push(...refusedKeys(connector.probe, ["balance"], [...at, "probe"], "a Custom API has no paid balance"))
  out.push(...headers(connector.headers, [...at, "headers"], authHeaderName(auth)))
  return out
}

function functionProblems(fn: unknown, i: number, auth: string | null): Problem[] {
  if (!isObject(fn)) return []
  const at = ["functions", i]
  const out = foreignKeys(fn, FUNCTION_KEYS, at, FOREIGN)
  const call = fn.call
  out.push(...refusedKeys(call, ["files"], [...at, "call"], "a Custom API sends JSON, never a file"))
  out.push(...refusedKeys(fn.output, ["file"], [...at, "output"], "a Custom API renders JSON, never a file"))
  if (isObject(call)) {
    out.push(...headers(call.headers, [...at, "call", "headers"], auth))
    const constants = isObject(call.constants) ? call.constants.headers : undefined
    out.push(...headers(constants, [...at, "call", "constants", "headers"], auth))
  }
  if (!Array.isArray(fn.examples) || fn.examples.length === 0)
    out.push(problem(EXAMPLE_MISSING, dotted([...at, "examples"]), "each action has at least one example, which its input accepts"))
  return out
}

/** Le texte JSON tel que Python l'écrit (`json.dumps`, séparateurs `, ` et `: `), pour mesurer le même poids. */
function pythonJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(pythonJson).join(", ")}]`
  if (isObject(value))
    return `{${Object.entries(value)
      .map(([key, item]) => `${JSON.stringify(key)}: ${pythonJson(item)}`)
      .join(", ")}}`
  const text = JSON.stringify(value)
  if (text === undefined) throw new TypeError("not a JSON value")
  return text
}

function sizeProblems(document: { [key: string]: Json }): Problem[] {
  const out: Problem[] = []
  const functions = document.functions
  if (Array.isArray(functions) && functions.length > MAX_FUNCTIONS)
    out.push(problem(TOO_LARGE, "functions", `${functions.length} functions, ${MAX_FUNCTIONS} at most`))
  let size: number
  try {
    size = new TextEncoder().encode(pythonJson(document)).length
  } catch {
    return [...out, problem(SCHEMA_INVALID, "", "the definition is not a JSON document")]
  }
  if (size > MAX_BYTES) out.push(problem(TOO_LARGE, "", `the definition weighs ${size} bytes, ${MAX_BYTES} at most`))
  return out
}

/** Une erreur du schéma, regroupée comme la lit le côté Python : une par mot et par place, avec ses clés. */
type SchemaError = { path: PathPart[]; keyword: string; message: string; keys: string[] }

function pointer(instancePath: string): PathPart[] {
  if (!instancePath) return []
  return instancePath
    .split("/")
    .slice(1)
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"))
    .map((part) => (/^(0|[1-9]\d*)$/.test(part) ? Number(part) : part))
}

const under = (path: readonly PathPart[], root: readonly PathPart[]) =>
  root.length <= path.length && root.every((part, i) => String(part) === String(path[i]))

/**
 * Les erreurs d'un validateur ramenées à celles que rend `jsonschema` côté Python : une erreur de `oneOf` ou `anyOf` tient
 * lieu de celles de ses branches (toute erreur à sa place ou sous elle) ; `if` ne compte pas (les erreurs de `then` et
 * `else` sont rendues pour elles-mêmes) ; `required` et `additionalProperties` sont regroupés par place, avec leurs clés.
 */
function schemaErrors(issues: readonly SchemaIssue[]): SchemaError[] {
  const branches = issues.filter((issue) => issue.keyword === "oneOf" || issue.keyword === "anyOf").map((i) => pointer(i.instancePath))
  const grouped = new Map<string, SchemaError>()
  const out: SchemaError[] = []
  for (const issue of issues) {
    if (issue.keyword === "if") continue
    const path = pointer(issue.instancePath)
    const branch = issue.keyword === "oneOf" || issue.keyword === "anyOf"
    if (!branch && branches.some((root) => under(path, root))) continue
    const key = issue.keyword === "required" ? issue.params?.missingProperty : issue.keyword === "additionalProperties" ? issue.params?.additionalProperty : undefined
    if (key !== undefined) {
      const id = JSON.stringify([issue.instancePath, issue.keyword])
      const known = grouped.get(id)
      if (known !== undefined) {
        if (!known.keys.includes(key)) known.keys.push(key)
        continue
      }
      const error = { path, keyword: issue.keyword, message: issue.message ?? issue.keyword, keys: [key] }
      grouped.set(id, error)
      out.push(error)
      continue
    }
    if (branch && out.some((e) => e.keyword === issue.keyword && dotted(e.path) === dotted(path))) continue
    out.push({ path, keyword: issue.keyword, message: issue.message ?? issue.keyword, keys: [] })
  }
  const sortKey = (e: SchemaError) => e.path.map(String)
  return out.sort((a, b) => {
    const x = sortKey(a)
    const y = sortKey(b)
    for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return (x[i] as string) < (y[i] as string) ? -1 : 1
    return x.length - y.length
  })
}

/** Un `required` qui ne demande que des clés que le profil refuse à cette place : le refus de leur cause le dit déjà. */
function asksRefusedKeys(error: SchemaError): boolean {
  if (error.keyword !== "required") return false
  const at = error.path
  let admitted: readonly string[] | undefined
  if (at.length === 0) admitted = TOP_KEYS
  else if (at.length === 1 && at[0] === "connector") admitted = CONNECTOR_KEYS
  else if (at.length === 2 && at[0] === "functions") admitted = FUNCTION_KEYS
  return admitted !== undefined && error.keys.length > 0 && error.keys.every((name) => !admitted.includes(name))
}

function errorPaths(error: SchemaError): PathPart[][] {
  return error.keys.length ? error.keys.map((key) => [...error.path, key]) : [error.path]
}

function covered(path: readonly PathPart[], named: readonly string[], branches: boolean): boolean {
  const text = dotted(path)
  return named.some(
    (other) => other === "" || text === other || text.startsWith(`${other}.`) || (branches && text !== "" && other.startsWith(`${text}.`)),
  )
}

function schemaProblems(document: unknown, named: readonly string[], compile: SchemaCompiler): [Problem[], boolean] {
  const issues = compile(connectorSchema)(document)
  const out: Problem[] = []
  for (const error of schemaErrors(issues)) {
    const branches = error.keyword === "oneOf" || error.keyword === "anyOf"
    if (asksRefusedKeys(error) || errorPaths(error).every((path) => covered(path, named, branches))) continue
    out.push(problem(SCHEMA_INVALID, dotted(error.path), error.message))
  }
  return [out, issues.length === 0]
}

function exampleProblems(document: Json, compile: SchemaCompiler): Problem[] {
  const out: Problem[] = []
  document.functions.forEach((fn: Json, i: number) => {
    const check = compile(fn.input)
    fn.examples.forEach((example: Json, j: number) => {
      const first = check(example.input)[0]
      if (first !== undefined)
        out.push(
          problem(
            EXAMPLE_INVALID,
            dotted(["functions", i, "examples", j]),
            `the example ${repr(example.title)} does not match the input: ${first.message ?? first.keyword}`,
          ),
        )
    })
  })
  return out
}

function rule(at: string, judge: () => void): Problem[] {
  try {
    judge()
  } catch (broken) {
    if (!(broken instanceof DescriptionError)) throw broken
    const message = broken.message.startsWith(`${WHERE}: `) ? broken.message.slice(WHERE.length + 2) : broken.message
    const code = CODED_RULES.find((c) => message.includes(`: ${c}: `))
    return [problem(RULE_BROKEN, at, message, code)]
  }
  return []
}

function ruleProblems(document: Json): Problem[] {
  const connector = document.connector
  const out = rule("connector.auth", () => checkAuth(connector, WHERE))
  document.functions.forEach((fn: Json, i: number) => {
    const at = dotted(["functions", i])
    out.push(...rule(at, () => checkArguments(fn, WHERE)))
    out.push(...rule(at, () => checkConstants(connector, fn, WHERE)))
    out.push(...rule(at, () => checkChecks(fn, WHERE)))
    out.push(...rule(at, () => checkAct(fn, WHERE)))
    out.push(...rule(at, () => checkTimeout(connector, fn, WHERE)))
    out.push(...rule(at, () => checkPersonal(fn, WHERE)))
    const strip = (fn.output || {}).strip
    const refused = strip && strip.length ? stripRefusal(strip) : null
    if (refused !== null) {
      const [code, path, reason] = refused
      out.push(problem(RULE_BROKEN, `${at}.output.strip`, `${repr(path)}: ${code}: ${reason}`, code))
    }
  })
  const names: string[] = document.functions.map((fn: Json) => fn.name)
  const twice = [...new Set(names.filter((name, i) => names.indexOf(name) !== i))].sort()
  out.push(...twice.map((name) => problem(RULE_BROKEN, "functions", `function name ${repr(name)} declared twice`)))
  out.push(...rule("connector", () => checkProbe(document, WHERE)))
  out.push(...rule("connector.errors", () => checkSignals(document, WHERE)))
  return out
}

/** Tous les problèmes d'une définition Custom API, dans l'ordre des juges ; `[]` quand elle peut s'enregistrer. */
export function validate(document: unknown, compile: SchemaCompiler): Problem[] {
  if (!isObject(document)) return [problem(SCHEMA_INVALID, "", "a definition is an object with connector and functions")]
  const out = sizeProblems(document)
  out.push(...foreignKeys(document, TOP_KEYS, [], "a definition is a connector and its functions, nothing else"))
  out.push(...connectorProblems(document.connector))
  const auth = authHeaderName(isObject(document.connector) ? document.connector.auth : undefined)
  if (Array.isArray(document.functions)) document.functions.forEach((fn: unknown, i: number) => out.push(...functionProblems(fn, i, auth)))
  if (out.some((p) => p.code === TOO_LARGE)) return unique(out)
  const [found, matches] = schemaProblems(document, out.map((p) => p.path), compile)
  out.push(...found)
  if (!matches) return unique(out)
  out.push(...exampleProblems(document, compile))
  if (!out.length) out.push(...ruleProblems(document))
  return unique(out)
}
