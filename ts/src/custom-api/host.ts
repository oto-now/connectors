// Le profil hôte : les fonctions de l'API propre d'un hôte, décrite en OpenAPI, servies à côté de celles d'oto sous le
// jeton de la personne (docs/conception/profil-hote.md). Copie de `oto/connectors/custom_api/host.py`, jouée sur les mêmes
// cas communs (`connectors/_cases/host.yaml`).
//
// `fromHostOpenApi(document, { compile, reserved, yaml? })` rend `{ definition, problems }` : une définition, ou `null` et
// tous les problèmes du document, chacun avec son code, sa place et son message. `reserved` : les espaces de noms que
// l'appelant sert déjà (ses fonctions, ses connecteurs), qu'aucun espace de l'hôte ne reprend. La définition :
//
//   { host: { functions: "/me/functions", roles, families },
//     functions: { "projet.lister": { label, family, roles, summary, scope } },   // dans l'ordre du document
//     connectors: [ un document du format par espace de noms, dans l'ordre d'apparition ] }
//
// Chaque opération est une fonction nommée par son `operationId`, `espace.verbe`, gardé tel quel : l'espace est son
// connecteur, le verbe son nom. Sa classe, son effet, ses exemples et sa sortie sont ses extensions `x-oto-*` ; sa
// description est celle de l'opération. Chaque connecteur lit l'adresse de l'hôte dans le réglage `address`, suivie du
// serveur relatif du document, et s'authentifie en `host_delegated`. Une fonction sensible se confirme par les deux temps
// de l'API (`confirm: { by: host }`) : `confirm`, paramètre de query réservé à ce protocole, n'est jamais un argument.
// Une famille d'`x-oto` est son libellé, ou `{ label, scope }` : le scope OAuth demandé pour le jeton de ses fonctions,
// que chacune porte (`scope`, `null` pour une famille sans scope).

import { internalHost } from "./address"
import { standardErrors } from "./errors"
import { SERVER_VARIABLES } from "./openapi-connector"
import { identifier } from "./naming"
import { OpenAPIRefused, Skip, parameters, parse, parts, walk, type Parts } from "./openapi"
import { RefRefused } from "./openapi-refs"
import { EXAMPLE_INVALID as FORMAT_EXAMPLE_INVALID, RULE_BROKEN as FORMAT_RULE_BROKEN, SCHEMA_INVALID as FORMAT_SCHEMA_INVALID } from "./problems"
import { problem, unique, type Problem } from "./problems"
import { exampleProblems, ruleProblems, schemaProblems, type SchemaCompiler } from "./profile"
import { isObject, repr } from "./rules"

type Json = any

export const OPENAPI_VERSION = "host_openapi_version"
export const SERVER_NOT_RELATIVE = "host_server_not_relative"
export const ROOT_INVALID = "host_root_invalid"
export const EXTENSION_UNKNOWN = "host_extension_unknown"
export const EXTENSION_MISSING = "host_extension_missing"
export const EXTENSION_INVALID = "host_extension_invalid"
export const OPERATION_ID_INVALID = "host_operation_id_invalid"
export const NAME_COLLISION = "host_name_collision"
export const NAMESPACE_RESERVED = "host_namespace_reserved"
export const NAMESPACE_ACT = "host_namespace_act"
export const SUMMARY_INVALID = "host_summary_invalid"
export const DESCRIPTION_INVALID = "host_description_invalid"
export const CLASS_MISMATCH = "host_class_mismatch"
export const INPUT_NOT_CLOSED = "host_input_not_closed"
export const EXAMPLE_MISSING = "host_example_missing"
export const EXAMPLE_INVALID = "host_example_invalid"
export const CONFIRM_RESERVED = "host_confirm_reserved"
export const CONFIRM_MISSING = "host_confirm_missing"
export const SCHEMA_INVALID = "host_schema_invalid"
export const RULE_BROKEN = "host_rule_broken"
export const SCOPE_INVALID = "host_scope_invalid"
/** Les codes du profil lui-même ; un document reçoit aussi ceux de l'import (`openapi_*`). */
export const HOST_CODES = [
  OPENAPI_VERSION, SERVER_NOT_RELATIVE, ROOT_INVALID, EXTENSION_UNKNOWN, EXTENSION_MISSING, EXTENSION_INVALID,
  OPERATION_ID_INVALID, NAME_COLLISION, NAMESPACE_RESERVED, NAMESPACE_ACT, SUMMARY_INVALID, DESCRIPTION_INVALID,
  CLASS_MISMATCH, INPUT_NOT_CLOSED, EXAMPLE_MISSING, EXAMPLE_INVALID, CONFIRM_RESERVED, CONFIRM_MISSING, SCHEMA_INVALID,
  RULE_BROKEN, SCOPE_INVALID,
] as const
/** Les juges du format, leurs codes dits dans ceux du profil. */
const JUDGED: { [code: string]: string } = {
  [FORMAT_SCHEMA_INVALID]: SCHEMA_INVALID,
  [FORMAT_EXAMPLE_INVALID]: EXAMPLE_INVALID,
  [FORMAT_RULE_BROKEN]: RULE_BROKEN,
}

const OPERATION_ID = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/
const IDENTIFIER = /^[a-z][a-z0-9_]*$/
const URI = /^[a-z][a-z0-9+.-]*:\S+$/
/** Un scope OAuth (RFC 6749 §3.3) : des jetons d'ASCII imprimable sauf l'espace, `"` et `\`, séparés d'une espace. */
const SCOPE = /^[\x21\x23-\x5B\x5D-\x7E]+( [\x21\x23-\x5B\x5D-\x7E]+)*$/
const FUNCTIONS_PATH = /^\/[^/{}?#\s][^{}?#\s]*$/
const MAX_OPERATION_ID = 64
const MAX_SUMMARY = 90
const MAX_DESCRIPTION = 1000
const ROOT = "x-oto"
const ROOT_KEYS = ["functions", "issuer", "resource", "roles", "families"]
const FAMILY_KEYS = ["label", "scope"]
const EXTENSIONS = ["x-oto-class", "x-oto-effect", "x-oto-examples", "x-oto-roles", "x-oto-label", "x-oto-family", "x-oto-output"]
const REQUIRED = ["x-oto-class", "x-oto-roles", "x-oto-label", "x-oto-family"]
const CLASSES = ["read", "write", "sensitive"]
const EFFECTS = ["send", "destroy"]
const OUTPUT_KEYS = ["items", "strip", "projection"]
const ACT_SUFFIX = "_act"
/** Le paramètre de query des deux temps d'une fonction sensible, jamais un argument. */
const CONFIRM = "confirm"
const SETTING = { name: "address", label: "Address of the host", type: "url" }
const VERSION = "1.0.0"

/** Ce que le profil reçoit de l'hôte : le validateur JSON Schema, les espaces déjà servis, le lecteur YAML s'il en faut. */
export type HostOptions = {
  readonly compile: SchemaCompiler
  readonly reserved: Iterable<string>
  readonly yaml?: (text: string) => unknown
}

export type HostProblem = Problem<string>
export type HostResult = { definition: Json | null; problems: HostProblem[] }

const blank = (value: unknown): boolean => typeof value !== "string" || !value.trim()
const chars = (text: string): number => Array.from(text.trim()).length

/** Une table d'identifiants vers des textes, non vide : les rôles de la racine. */
const labels = (value: unknown): boolean =>
  isObject(value) && Object.keys(value).length > 0 && Object.entries(value).every(([key, text]) => IDENTIFIER.test(key) && !blank(text))

/** Les familles de la racine, chacune un libellé ou `{ label, scope }` ; `null` sur un défaut, chacun nommé. */
function familiesOf(value: unknown, problems: HostProblem[]): Json | null {
  const at = `${ROOT}.families`
  if (!isObject(value) || !Object.keys(value).length || !Object.keys(value).every((key) => IDENTIFIER.test(key))) {
    problems.push(problem(ROOT_INVALID, at, "the families, a table of identifiers to a label or to {label, scope}, not empty"))
    return null
  }
  const found = problems.length
  for (const [key, family] of Object.entries(value)) {
    if (typeof family === "string") {
      if (blank(family)) problems.push(problem(ROOT_INVALID, `${at}.${key}`, "the label of the family, not blank"))
      continue
    }
    if (!isObject(family)) {
      problems.push(problem(ROOT_INVALID, `${at}.${key}`, "a label, or {label, scope}"))
      continue
    }
    for (const name of Object.keys(family))
      if (!FAMILY_KEYS.includes(name))
        problems.push(problem(EXTENSION_UNKNOWN, `${at}.${key}.${name}`, `${repr(name)} is not a key of a family (${FAMILY_KEYS.join(", ")})`))
    if (blank(family.label)) problems.push(problem(ROOT_INVALID, `${at}.${key}.label`, "the label of the family, not blank"))
    const scope = family.scope
    if (!(typeof scope === "string" && SCOPE.test(scope)))
      problems.push(
        problem(
          SCOPE_INVALID,
          `${at}.${key}.scope`,
          `${repr(scope)} is not an OAuth scope (RFC 6749 §3.3): tokens of printable ASCII but " and \\, separated by one space; a family without a scope is written as its label alone`,
        ),
      )
  }
  return problems.length > found ? null : { ...value }
}

/** Le scope OAuth d'une famille de la racine, `null` pour une famille écrite par son seul libellé. */
const scopeOf = (family: unknown): string | null => (isObject(family) ? family.scope : null)

function unknownExtensions(node: Json, at: string, known: readonly string[]): HostProblem[] {
  const where = at ? `${at}.` : ""
  return Object.keys(node)
    .filter((key) => key.startsWith("x-oto-") && !known.includes(key))
    .map((key) => problem(EXTENSION_UNKNOWN, `${where}${key}`, `${repr(key)} is not an extension oto reads here (${known.join(", ") || "none"})`))
}

function publicIssuer(issuer: unknown): boolean {
  if (typeof issuer !== "string" || /[{}?# ]/.test(issuer)) return false
  try {
    const url = new URL(issuer)
    return url.protocol === "https:" && Boolean(url.hostname) && !internalHost(url.hostname)
  } catch {
    return false
  }
}

/** Le bloc `x-oto`, chacun de ses défauts nommé ; les valeurs saines, par clé. */
function rootOf(document: Json, problems: HostProblem[]): Json {
  const root = document[ROOT]
  if (!isObject(root)) {
    problems.push(problem(ROOT_INVALID, ROOT, "the document has no x-oto block: functions, issuer, resource, roles, families"))
    return {}
  }
  for (const key of Object.keys(root))
    if (!ROOT_KEYS.includes(key)) problems.push(problem(EXTENSION_UNKNOWN, `${ROOT}.${key}`, `${repr(key)} is not a key of x-oto (${ROOT_KEYS.join(", ")})`))
  const sound: Json = {}
  if (typeof root.functions === "string" && FUNCTIONS_PATH.test(root.functions)) sound.functions = root.functions
  else problems.push(problem(ROOT_INVALID, `${ROOT}.functions`, "the path of the person's functions, relative to the address: /me/functions"))
  if (publicIssuer(root.issuer)) sound.issuer = root.issuer
  else
    problems.push(
      problem(ROOT_INVALID, `${ROOT}.issuer`, "the issuer of the tokens, a fixed https address towards a public host, as its tokens name it"),
    )
  if (typeof root.resource === "string" && URI.test(root.resource)) sound.resource = root.resource
  else problems.push(problem(ROOT_INVALID, `${ROOT}.resource`, "the resource indicator of the API at the issuer, an absolute URI"))
  if (labels(root.roles)) sound.roles = { ...root.roles }
  else problems.push(problem(ROOT_INVALID, `${ROOT}.roles`, "the roles, a table of identifiers to texts, not empty"))
  const families = familiesOf(root.families, problems)
  if (families !== null) sound.families = families
  return sound
}

/** Le serveur relatif du document, sans sa barre finale ; `""` sans serveur. */
function serverOf(document: Json, problems: HostProblem[]): string {
  if ("swagger" in document || !String(document.openapi ?? "").startsWith("3.")) {
    problems.push(problem(OPENAPI_VERSION, "openapi", "a host's API is described in OpenAPI 3.1 (3.0 accepted)"))
    return ""
  }
  const servers = document.servers
  if (servers === undefined || servers === null || (Array.isArray(servers) && servers.length === 0)) return ""
  if (!Array.isArray(servers) || servers.length !== 1 || !isObject(servers[0])) {
    problems.push(problem(SERVER_NOT_RELATIVE, "servers", "one relative server at most: the address is the organisation's, the document the same for all"))
    return ""
  }
  const url = servers[0].url
  if (typeof url === "string" && (url.includes("{") || (servers[0].variables !== undefined && Object.keys(servers[0].variables ?? {}).length))) {
    problems.push(problem(SERVER_VARIABLES, "servers.0", `${repr(url)} has variables, which a host's document has not: the address is a setting of the organisation`))
    return ""
  }
  if (typeof url !== "string" || !url.startsWith("/") || url.startsWith("//")) {
    problems.push(problem(SERVER_NOT_RELATIVE, "servers.0", `${repr(url)} is not a relative path (/api/v2): the address is a setting of the organisation`))
    return ""
  }
  return url.replace(/\/+$/, "")
}

function texts(operation: Json, at: string, problems: HostProblem[]): void {
  const { summary, description } = operation
  if (blank(summary) || chars(summary) > MAX_SUMMARY)
    problems.push(problem(SUMMARY_INVALID, `${at}.summary`, `the line of the index, 1 to ${MAX_SUMMARY} characters`))
  if (blank(description) || chars(description) > MAX_DESCRIPTION)
    problems.push(problem(DESCRIPTION_INVALID, `${at}.description`, `the contract the assistant reads, 1 to ${MAX_DESCRIPTION} characters`))
}

/** Les défauts des extensions `x-oto-*` de l'opération, ses exemples à part. */
function extensions(operation: Json, method: string, at: string, root: Json, problems: HostProblem[]): void {
  problems.push(...unknownExtensions(operation, at, EXTENSIONS))
  for (const key of REQUIRED) if (!(key in operation)) problems.push(problem(EXTENSION_MISSING, `${at}.${key}`, `${key} is required`))
  const cls = operation["x-oto-class"]
  if ("x-oto-class" in operation && !CLASSES.includes(cls))
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-class`, `${repr(cls)} is not a class (${CLASSES.join(", ")})`))
  else if (CLASSES.includes(cls) && (cls === "read") !== (method === "get"))
    problems.push(
      problem(CLASS_MISMATCH, `${at}.x-oto-class`, `${method.toUpperCase()} is ${repr(cls)}: GET reads, the other verbs write (write or sensitive)`),
    )
  if ("x-oto-effect" in operation && (!EFFECTS.includes(operation["x-oto-effect"]) || cls !== "sensitive"))
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-effect`, `a sensitive function's effect (${EFFECTS.join(", ")})`))
  if ("x-oto-label" in operation && blank(operation["x-oto-label"]))
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-label`, "the label shown to the person, not blank"))
  const family = operation["x-oto-family"]
  if ("x-oto-family" in operation && "families" in root && !(typeof family === "string" && Object.hasOwn(root.families, family)))
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-family`, `${repr(family)} is not a family of x-oto`))
  const roles = operation["x-oto-roles"]
  if (
    "x-oto-roles" in operation &&
    (!Array.isArray(roles) ||
      !roles.length ||
      new Set(roles.map(String)).size !== roles.length ||
      ("roles" in root && roles.some((role) => !(typeof role === "string" && Object.hasOwn(root.roles, role)))))
  )
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-roles`, "a list of roles of x-oto, each once, not empty"))
  const output = operation["x-oto-output"]
  if ("x-oto-output" in operation && (!isObject(output) || Object.keys(output).some((k) => !OUTPUT_KEYS.includes(k))))
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-output`, `the output block: ${OUTPUT_KEYS.join(", ")}`))
}

/** Les exemples, leurs clés passées des noms de l'API à ceux des arguments (une clé inconnue reste, l'entrée la refuse). */
function examplesOf(operation: Json, args: { [api: string]: string }, at: string, problems: HostProblem[]): Json[] {
  const examples = operation["x-oto-examples"]
  if (examples === undefined || examples === null || (Array.isArray(examples) && examples.length === 0)) {
    problems.push(problem(EXAMPLE_MISSING, `${at}.x-oto-examples`, "at least one example, the arguments as the assistant passes them"))
    return []
  }
  if (!Array.isArray(examples) || !examples.every(isObject)) {
    problems.push(problem(EXTENSION_INVALID, `${at}.x-oto-examples`, "a list of arguments, each an object"))
    return []
  }
  return examples.map((example: Json, i: number) => ({
    title: `Example ${i + 1}`,
    input: Object.fromEntries(Object.entries(example).map(([key, value]) => [args[key] ?? key, value])),
  }))
}

/** Les noms, autres que le paramètre de query réservé lui-même, qui porteraient `confirm` : tout argument (chemin, query,
 * en-tête, corps) ou cookie dont le nom, normalisé comme celui d'un argument, vaut `confirm` (`Confirm`, `CONFIRM`…). */
function impostors(document: Json, item: Json, operation: Json, found: Parts): string[] {
  const names = [
    ...Object.keys(found.arguments),
    ...parameters(document, item, operation)
      .filter((parameter) => parameter.in === "cookie")
      .map((parameter) => parameter.name),
  ]
  return names.filter((name) => identifier(String(name), "arg") === CONFIRM)
}

function confirmOf(document: Json, item: Json, operation: Json, found: Parts, cls: unknown, at: string, problems: HostProblem[]): void {
  const parameter = found.leftOut[CONFIRM]
  for (const name of impostors(document, item, operation, found))
    problems.push(
      problem(CONFIRM_RESERVED, at, `${repr(name)} would carry confirm, which is reserved to the protocol of the two steps: the query parameter confirm only`),
    )
  if (cls !== "sensitive") {
    if (parameter !== undefined) problems.push(problem(CONFIRM_RESERVED, `${at}.parameters`, "confirm is the parameter of a sensitive function only"))
    return
  }
  if (parameter === undefined || ((parameter.schema || {}).type ?? parameter.type) !== "boolean")
    problems.push(problem(CONFIRM_MISSING, `${at}.parameters`, "a sensitive function declares confirm, a boolean query parameter"))
  if (!Object.keys(operation.responses || {}).includes("202"))
    problems.push(
      problem(CONFIRM_MISSING, `${at}.responses`, "a sensitive function answers 202 { status: needs_confirmation, summary } when called without confirm"),
    )
}

type Built = { at: string; name: unknown; namespace: string | null; function?: Json; meta?: Json; problems: HostProblem[] }

function operationOf(document: Json, path: string, method: string, item: Json, operation: Json, root: Json, reserved: ReadonlySet<string>): Built {
  const at = `paths.${path}.${method}`
  const problems: HostProblem[] = []
  if ("servers" in item || "servers" in operation)
    problems.push(problem(SERVER_NOT_RELATIVE, at, "a server per path or operation is not followed: one document, one address"))
  const name = operation.operationId
  let namespace: string | null = null
  let verb: string | null = null
  if (typeof name !== "string" || !OPERATION_ID.test(name) || name.length > MAX_OPERATION_ID)
    problems.push(
      problem(
        OPERATION_ID_INVALID,
        `${at}.operationId`,
        `${repr(name ?? null)} is not namespace.verb, in lowercase letters, digits and _, ${MAX_OPERATION_ID} characters at most`,
      ),
    )
  else {
    ;[namespace = "", verb = ""] = name.split(".")
    if (reserved.has(namespace)) problems.push(problem(NAMESPACE_RESERVED, `${at}.operationId`, `the namespace ${repr(namespace)} is already served`))
    if (namespace.endsWith(ACT_SUFFIX))
      problems.push(problem(NAMESPACE_ACT, `${at}.operationId`, `${repr(namespace)} ends with ${repr(ACT_SUFFIX)}, the suffix of the side that acts`))
  }
  texts(operation, at, problems)
  extensions(operation, method, at, root, problems)
  let found: Parts
  try {
    found = parts(document, path, method, item, operation, [CONFIRM])
  } catch (refused) {
    if (!(refused instanceof Skip || refused instanceof RefRefused)) throw refused
    problems.push(problem(refused.code, at, refused.reason))
    return { at, name, namespace: null, problems }
  }
  const cls = operation["x-oto-class"]
  confirmOf(document, item, operation, found, cls, at, problems)
  if (found.body !== null && found.body.additionalProperties !== false)
    problems.push(problem(INPUT_NOT_CLOSED, `${at}.requestBody`, "the JSON body is closed: additionalProperties: false"))
  const examples = examplesOf(operation, found.arguments, at, problems)
  const fn: Json = { name: verb, class: cls }
  if ("x-oto-effect" in operation) fn.effect = operation["x-oto-effect"]
  fn.description = String(operation.description || "").trim()
  fn.input = { type: "object", additionalProperties: false, properties: found.properties }
  if (found.required.length) fn.input.required = [...new Set(found.required)]
  fn.examples = examples
  const call: Json = { method: method.toUpperCase(), path: found.path }
  for (const [where, table] of Object.entries(found.places)) if (Object.keys(table).length) call[where] = table
  fn.call = call
  if (isObject(operation["x-oto-output"])) fn.output = { ...operation["x-oto-output"] }
  if (cls === "sensitive") fn.confirm = { by: "host" }
  const family = operation["x-oto-family"]
  const meta = {
    label: operation["x-oto-label"],
    family,
    roles: operation["x-oto-roles"],
    summary: String(operation.summary || "").trim(),
    scope: typeof family === "string" ? scopeOf(Object.hasOwn(root.families ?? {}, family) ? root.families[family] : null) : null,
  }
  return { at, name, namespace, function: fn, meta, problems }
}

/** Les juges du format sur un connecteur construit : le schéma, les exemples, les règles ; chaque problème dit à la place
 * de son opération dans le document OpenAPI, son code dans ceux du profil. */
function judged(document: Json, places: readonly string[], compile: SchemaCompiler): HostProblem[] {
  let [found, matches] = schemaProblems(document, [], compile) as [HostProblem[], boolean]
  if (matches) {
    found = exampleProblems(document, compile)
    if (!found.length) found = ruleProblems(document)
  }
  return found.map((p) => {
    let path = p.path
    const head = /^functions\.(\d+)(\..*)?$/.exec(path)
    if (head) path = (places[Number(head[1])] ?? path) + (head[2] ?? "")
    return problem(JUDGED[p.code] ?? p.code, path, p.message, p.rule)
  })
}

function connectorOf(namespace: string, info: Json, server: string, root: Json): Json {
  const title = String(info.title).trim()
  return {
    name: namespace,
    label: title,
    namespace,
    version: VERSION,
    api_version: String(info.version),
    base_url: `{${SETTING.name}}${server}`,
    settings: [{ ...SETTING }],
    auth: { kind: "host_delegated", issuer: root.issuer, resource: root.resource },
    modes: ["byo_user"],
    errors: standardErrors(title, "host_delegated"),
  }
}

/** La définition que donne le document OpenAPI d'un hôte, ou ses problèmes (en-tête du module). */
export function fromHostOpenApi(document: unknown, options: HostOptions): HostResult {
  let doc: Json
  try {
    doc = parse(document, options.yaml)
  } catch (refused) {
    if (!(refused instanceof OpenAPIRefused)) throw refused
    return { definition: null, problems: [problem(refused.code, "", refused.message)] }
  }
  const problems: HostProblem[] = []
  const server = serverOf(doc, problems)
  const root = rootOf(doc, problems)
  const info = isObject(doc.info) ? doc.info : {}
  for (const key of ["title", "version"]) if (blank(String(info[key] || ""))) problems.push(problem(ROOT_INVALID, `info.${key}`, `the document's ${key}`))
  problems.push(...unknownExtensions(doc, "", []))
  const reserved = new Set(options.reserved)
  const [built, skipped] = walk(doc, (path, method, item, operation) => operationOf(doc, path, method, item, operation, root, reserved))
  for (const s of skipped)
    problems.push(problem(s.code, `paths.${s.path}` + (s.method === "*" ? "" : `.${s.method.toLowerCase()}`), s.reason))
  const seen = new Set<string>()
  const connectors = new Map<string, { functions: Json[]; places: string[] }>()
  for (const operation of built) {
    problems.push(...operation.problems)
    if (operation.namespace === null) continue
    const name = operation.name as string
    if (seen.has(name)) {
      problems.push(problem(NAME_COLLISION, `${operation.at}.operationId`, `${repr(name)} is declared twice`))
      continue
    }
    seen.add(name)
    const group = connectors.get(operation.namespace) ?? { functions: [], places: [] }
    group.functions.push(operation.function)
    group.places.push(operation.at)
    connectors.set(operation.namespace, group)
  }
  if (problems.length) return { definition: null, problems: unique(problems) }
  const documents: Json[] = []
  for (const [namespace, group] of connectors) {
    const built = { connector: connectorOf(namespace, info, server, root), functions: group.functions }
    problems.push(...judged(built, group.places, options.compile))
    documents.push(built)
  }
  if (problems.length) return { definition: null, problems: unique(problems) }
  return {
    definition: {
      host: { functions: root.functions, roles: root.roles, families: root.families },
      functions: Object.fromEntries(built.map((operation) => [operation.name, operation.meta])),
      connectors: documents,
    },
    problems: [],
  }
}
