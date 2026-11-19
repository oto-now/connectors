// La projection d'un document du format dans la définition que la fabrique génère au build (`fabrique/typescript.py`) :
// `toDefinition(doc)` rend ce qu'exporte `ts/src/<nom>.ts` sous `connector`, ses fonctions en ligne. Une garde
// (`test/custom-api.test.ts`) l'applique à chaque `connectors/*/connector.yaml` et la compare au module généré : les deux
// projections ne divergent pas. Une définition Custom API se valide d'abord (`validate`) ; la projection ne juge rien,
// sauf ce que la fabrique refuse de générer, qu'elle lève (`NotGenerated`) au lieu de l'omettre.

import type { Connector, ConnectorFunction } from "../types"
import { DEFAULT_TIMEOUT_S, act, pathParams } from "./rules"

type Json = any

/** Une fonction que la fabrique ne génère pas, avec sa raison. */
export class NotGenerated extends Error {}

const DEFAULT_MAX_BYTES = 52_428_800
const JSON_MAX_BYTES = 4_194_304
const PAGINATION_ARGUMENTS = ["all_pages", "max_pages"]

export function camel(name: string): string {
  const [head = "", ...rest] = name.split("_")
  return head + rest.map((part) => part.slice(0, 1).toUpperCase() + part.slice(1)).join("")
}

const copy = <T>(value: T): T => structuredClone(value)

/** Les clés présentes de `source`, dans l'ordre donné. */
function pick(source: Json, keys: readonly string[]): { [key: string]: Json } {
  return Object.fromEntries(keys.filter((key) => key in source).map((key) => [key, copy(source[key])]))
}

/** L'entrée servie : celle de la description, plus `all_pages` et `max_pages` quand la fonction pagine. */
function servedInput(fn: Json): Json {
  const served = copy(fn.input)
  if (served.type !== "object" || served.additionalProperties !== false)
    throw new NotGenerated("input: the root must be a strict object (type: object, additionalProperties: false)")
  const pagination = fn.pagination
  if (!pagination) return served
  const taken = PAGINATION_ARGUMENTS.filter((name) => name in served.properties)
  if (taken.length) throw new NotGenerated(`input declares ${taken.join(", ")}, which pagination adds`)
  const limit = pagination.max_pages
  served.properties.all_pages = {
    type: "boolean",
    description: `Read the following pages too, up to max_pages (at most ${limit}).`,
  }
  served.properties.max_pages = { type: "integer", minimum: 1, maximum: limit, default: limit, description: "Pages read when all_pages is true." }
  return served
}

/** Le signal de refus du service d'une entrée de la table ou d'un refus : sa condition sur le corps et la marque. */
function signal(entry: Json): Json {
  const out: Json = {}
  if ("body" in entry) out.body = copy(entry.body)
  if ("marks_account" in entry) out.marksAccount = entry.marks_account
  return out
}

function request(call: Json): Json {
  const out: Json = {
    method: call.method,
    path: call.path,
    pathParams: pathParams(call.path),
    query: copy(call.query || {}),
    body: copy(call.body || {}),
    headers: copy(call.headers || {}),
  }
  if (call.body_list === true) out.bodyList = true
  for (const key of ["files", "constants", "encode"]) if (key in call) out[key] = copy(call[key])
  return out
}

function pagination(declared: Json): Json {
  const out: Json = { kind: declared.kind, requestParam: declared.request_param, next: copy(declared.next) }
  if ("more" in declared) out.more = copy(declared.more)
  if ("total" in declared) out.total = copy(declared.total)
  out.maxPages = declared.max_pages
  return out
}

/** Le fichier rendu, défauts résolus (`auth: none` d'une adresse, `maxBytes`), comme `output_file` côté fabrique. */
function outputFile(file: Json): Json {
  const out: Json = { from: file.from }
  if ("path" in file) out.path = file.path
  if (file.from === "url" || "auth" in file) out.auth = file.auth ?? "none"
  if ("types" in file) out.types = copy(file.types)
  out.name = pick(file.name, ["header", "path", "template"])
  out.maxBytes = file.max_bytes ?? (file.from === "json" ? JSON_MAX_BYTES : DEFAULT_MAX_BYTES)
  return out
}

function output(declared: Json): Json {
  const out = pick(declared, ["items", "strip", "projection", "schema"])
  if (declared.file !== undefined && declared.file !== null) out.file = outputFile(declared.file)
  return out
}

function cost(declared: Json): Json {
  const out = pick(declared, ["per", "quantity", "items", "times"])
  if ("weights" in declared) out.weights = { field: declared.weights.field, values: copy(declared.weights.values) }
  if ("unit" in declared) out.unit = declared.unit
  if ("settle" in declared) out.settle = pick(declared.settle, ["path", "scale"])
  return out
}

function asyncJob(connector: string, declared: Json): Json {
  const out: Json = {
    collect: `${connector}.${declared.collect}`,
    jobId: { from: declared.job_id.from, to: declared.job_id.to },
  }
  if ("within_s" in declared) out.withinMs = declared.within_s * 1000
  if (declared.no_deadline === true) out.noDeadline = true
  return out
}

/** La définition d'une fonction, ou `NotGenerated`. */
export function functionDefinition(connector: string, fn: Json): ConnectorFunction {
  if ("handwritten" in fn) throw new NotGenerated(`${connector}.${fn.name}: handwritten: the fabrique does not generate it`)
  let schema: Json
  try {
    schema = servedInput(fn)
  } catch (refusal) {
    if (refusal instanceof NotGenerated) throw new NotGenerated(`${connector}.${fn.name}: ${refusal.message}`)
    throw refusal
  }
  const out: Json = { name: `${connector}.${fn.name}`, connector, class: fn.class, act: act(fn) }
  if ("scopes" in fn) out.scopes = copy(fn.scopes)
  out.description = fn.description.trim()
  out.schema = schema
  out.examples = fn.examples.map((example: Json) => ({ title: example.title, input: copy(example.input) }))
  out.refusals = (fn.refusals ?? []).map((r: Json) => ({ code: r.code, when: copy(r.when), message: r.message, ...signal(r) }))
  if ("checks" in fn) out.checks = copy(fn.checks)
  if ("expect" in fn) out.expect = copy(fn.expect)
  out.request = request(fn.call)
  if ("timeout_s" in fn) out.timeoutMs = fn.timeout_s * 1000
  if (fn.background === true) out.background = true
  if ("async" in fn) out.async = asyncJob(connector, fn.async)
  if ("pagination" in fn) out.pagination = pagination(fn.pagination)
  if ("output" in fn) out.output = output(fn.output)
  if ("cost" in fn) out.cost = cost(fn.cost)
  if ("confirm" in fn) out.confirm = { summary: fn.confirm.summary }
  return out
}

/** Les clés de `auth` en camelCase, les valeurs telles quelles, sauf l'identité lue par une fonction, qualifiée, et les
 * clés de `exchange`. */
function auth(connector: string, declared: Json): Json {
  const out: Json = {}
  for (const [key, value] of Object.entries<Json>(declared)) {
    let projected = copy(value)
    if (key === "identity" && "function" in value) projected = { function: `${connector}.${value.function}`, path: value.path }
    else if (key === "exchange") projected = Object.fromEntries(Object.entries(value).map(([k, v]) => [camel(k), copy(v)]))
    out[camel(key)] = projected
  }
  return out
}

/** La définition d'un connecteur, telle que la fabrique l'exporte sous `connector` dans `ts/src/<nom>.ts`. */
export function toDefinition(doc: Json): Connector {
  const connector = doc.connector
  const name: string = connector.name
  const out: Json = {
    name,
    label: connector.label,
    namespace: connector.namespace,
    version: connector.version,
    apiVersion: connector.api_version,
  }
  if ("description" in connector) out.description = connector.description
  if ("logo_domain" in connector) out.logoDomain = connector.logo_domain
  if ("base_url" in connector) out.baseUrl = connector.base_url
  if ("base_urls" in connector) out.baseUrls = copy(connector.base_urls)
  out.auth = auth(name, connector.auth)
  out.credential = (connector.credential ?? []).map((field: Json) => ({
    name: field.name,
    label: field.label,
    secret: field.secret,
    holder: field.holder ?? "account",
  }))
  if ("account_identity" in connector) out.accountIdentity = copy(connector.account_identity)
  if ("settings" in connector) out.settings = copy(connector.settings)
  out.modes = copy(connector.modes)
  out.timeoutMs = (connector.timeout_s ?? DEFAULT_TIMEOUT_S) * 1000
  if ("query_arrays" in connector) out.queryArrays = connector.query_arrays
  if ("headers" in connector) out.headers = copy(connector.headers)
  if ("rate_limit" in connector)
    out.rateLimit = { requests: connector.rate_limit.requests, intervalMs: connector.rate_limit.seconds * 1000 }
  if ("quota" in connector) out.quota = copy(connector.quota)
  out.errors = connector.errors.map((e: Json) => ({
    status: copy(e.status),
    code: e.code,
    message: e.message,
    retryable: e.retryable,
    ...signal(e),
  }))
  if ("probe" in connector) {
    const probe = connector.probe
    out.probe = { function: `${name}.${probe.function}`, nonEmpty: copy(probe.non_empty ?? []) }
    if (probe.balance !== undefined && probe.balance !== null)
      out.probe.balance = { path: probe.balance.path, min: probe.balance.min, unit: probe.balance.unit }
  }
  if ("exposure" in doc) out.exposure = copy(doc.exposure)
  out.functions = doc.functions.map((fn: Json) => functionDefinition(name, fn))
  return out
}
