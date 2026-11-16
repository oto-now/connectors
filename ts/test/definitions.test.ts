// Chaque définition générée porte un nom qualifié et un JSON Schema d'entrée que valide un validateur JSON Schema 2020-12
// standard (Ajv) : ses exemples passent, une clé inconnue est refusée. `format` reste une annotation, comme dans le test
// des descriptions côté Python ; le mode strict d'Ajv signale tout mot-clé inconnu ou mal placé, sauf `personal` (une
// donnée personnelle, règle 14 de la fabrique), qu'il connaît comme le paquet de l'hôte qui ne la journalise pas.
import { Ajv2020 } from "ajv/dist/2020.js"
import { describe, expect, it } from "vitest"
import { connectors } from "../src"
import type { BodyMap } from "../src/types"

const ajv = new Ajv2020({ strictTypes: false, strictTuples: false, validateFormats: false, allErrors: true })
ajv.addKeyword({ keyword: "personal", schemaType: "boolean" })
/** Les entrées d'un corps en table, à toute profondeur (règle 22) : chaque clé côté API et chaque argument. */
const bodyKeys = (body: BodyMap): string[] => Object.entries(body).flatMap(([key, value]) => [key, ...(typeof value === "string" ? [] : bodyKeys(value))])
const bodyArguments = (body: BodyMap): string[] => Object.values(body).flatMap((value) => (typeof value === "string" ? [value] : bodyArguments(value)))

const functions = connectors.flatMap((connector) => connector.functions.map((fn) => [fn.name, connector, fn] as const))

describe("generated definitions", () => {
  it("should accept an input property marked personal, and refuse a mark that is not a boolean", () => {
    const schema = { type: "object", additionalProperties: false, properties: { email: { type: "string", personal: true } } }
    expect(ajv.validate(schema, { email: "someone@example.test" })).toBe(true)
    expect(() => ajv.compile({ ...schema, properties: { email: { type: "string", personal: "yes" } } })).toThrow()
  })

  it("should generate at least one function per connector", () => {
    for (const connector of connectors) expect(connector.functions.length, connector.name).toBeGreaterThan(0)
  })

  it.each(functions)("%s: qualified name, strict root, examples accepted, unknown key refused", (name, connector, fn) => {
    expect(name).toBe(`${connector.name}.${name.slice(connector.name.length + 1)}`)
    expect(fn.connector).toBe(connector.name)
    expect(fn.schema.type).toBe("object")
    expect(fn.schema.additionalProperties).toBe(false)
    const validate = ajv.compile(fn.schema)
    expect(fn.examples.length).toBeGreaterThan(0)
    for (const example of fn.examples) {
      expect(validate(example.input), `${example.title}: ${ajv.errorsText(validate.errors)}`).toBe(true)
    }
    const first = fn.examples[0]
    expect(validate({ ...first?.input, __unknown__: 1 })).toBe(false)
  })

  it.each(functions)("%s: every path parameter and mapped argument names a schema key", (_name, _connector, fn) => {
    const keys = Object.keys(fn.schema.properties as object)
    for (const param of fn.request.pathParams) expect(keys).toContain(param)
    // Un corps en texte est l'argument qui forme le corps entier (règle 20) ; en table, des noms côté API, emboîtés ou non
    // (règle 22).
    const body = typeof fn.request.body === "string" ? [fn.request.body] : bodyArguments(fn.request.body)
    const mapped = [...body, ...[fn.request.query, fn.request.headers, fn.request.files ?? {}].flatMap((names) => Object.values(names))]
    for (const argument of mapped) {
      expect(keys).toContain(argument)
    }
    for (const argument of Object.keys(fn.request.encode ?? {})) expect(keys).toContain(argument)
  })

  it.each(connectors.filter((c) => c.probe).map((c) => [c.name, c] as const))("%s: the probe is a read function callable with {}", (_name, connector) => {
    const probe = connector.functions.find((fn) => fn.name === connector.probe?.function)
    expect(probe?.class).toBe("read")
    expect(ajv.validate(probe?.schema as object, {})).toBe(true)
  })

  const consents = connectors.flatMap((c) => (c.auth.kind === "oauth2_user" ? [[c.name, c, c.auth] as const] : []))
  it.each(consents)("%s: the identity of a consent is a read function callable with {} or an https address, and no credential", (_name, connector, auth) => {
    if (auth.identity.url !== undefined) {
      expect(auth.identity.url).toMatch(/^https:\/\/[^\s{}?#]+$/)
      expect(auth.identity.scopes.length).toBeGreaterThan(0)
    } else {
      const identity = connector.functions.find((fn) => fn.name === auth.identity.function)
      expect(identity?.class).toBe("read")
      expect(ajv.validate(identity?.schema as object, {})).toBe(true)
    }
    expect(connector.credential).toEqual([])
  })

  // Niveaux d'accès : une fonction est servie à un niveau quand chacun de ses droits est un droit du niveau ou impliqué par
  // l'un d'eux. `full` sert tout ; l'identité du consentement est servie à chaque niveau ; sans niveaux, aucun droit.
  const covered = (scopes: readonly string[], implies: { readonly [scope: string]: readonly string[] }) =>
    new Set(scopes.flatMap((scope) => [scope, ...(implies[scope] ?? [])]))
  const leveled = consents.flatMap(([name, connector, auth]) => (auth.accessLevels ? [[name, connector, auth.accessLevels, auth.scopeImplies ?? {}, auth.identity] as const] : []))
  it.each(leveled)("%s: full serves every function, and every level serves the identity", (_name, connector, levels, implies, identity) => {
    const full = covered(levels.full.scopes, implies)
    for (const fn of connector.functions) {
      expect(fn.scopes?.length, fn.name).toBeGreaterThan(0)
      for (const scope of fn.scopes ?? []) expect(full.has(scope), `${fn.name}: ${scope}`).toBe(true)
    }
    const who = identity.url !== undefined ? identity.scopes : connector.functions.find((fn) => fn.name === identity.function)?.scopes
    expect(Object.keys(levels).length).toBeGreaterThan(1)
    for (const [level, { scopes }] of Object.entries(levels)) {
      const granted = covered(scopes, implies)
      for (const scope of who ?? ["(none)"]) expect(granted.has(scope), `${level}: ${scope}`).toBe(true)
    }
  })

  // Un compte de service à niveaux : `full` sert tout, la sonde est servie à chaque niveau, le niveau par défaut est déclaré.
  const serviceAccounts = connectors.flatMap((c) =>
    c.auth.kind === "oauth2_jwt_bearer" && c.auth.accessLevels ? [[c.name, c, c.auth.accessLevels, c.auth.scopeImplies ?? {}, c.auth.defaultLevel] as const] : [])
  it.each(serviceAccounts)("%s: a service account's levels: full serves every function, every level serves the probe", (_name, connector, levels, implies, defaultLevel) => {
    const full = covered(levels.full.scopes, implies)
    for (const fn of connector.functions) {
      expect(fn.scopes?.length, fn.name).toBeGreaterThan(0)
      for (const scope of fn.scopes ?? []) expect(full.has(scope), `${fn.name}: ${scope}`).toBe(true)
    }
    if (defaultLevel !== undefined) expect(Object.keys(levels)).toContain(defaultLevel)
    const probe = connector.functions.find((fn) => fn.name === connector.probe?.function)?.scopes
    for (const [level, { scopes }] of Object.entries(levels)) {
      const granted = covered(scopes, implies)
      for (const scope of probe ?? []) expect(granted.has(scope), `${level}: ${scope}`).toBe(true)
    }
  })

  it("google_search_console: read by default, add_sitemap at full only", () => {
    const gsc = serviceAccounts.find(([name]) => name === "google_search_console")
    if (gsc === undefined) throw new Error("google_search_console has no access levels")
    const [, connector, levels, implies, defaultLevel] = gsc
    expect(defaultLevel).toBe("read")
    const atRead = covered(levels.read?.scopes ?? [], implies)
    const served = connector.functions.filter((fn) => (fn.scopes ?? []).every((scope) => atRead.has(scope))).map((fn) => fn.name)
    expect(served).toEqual(connector.functions.filter((fn) => fn.class === "read").map((fn) => fn.name))
    expect(connector.functions.find((fn) => fn.name === "google_search_console.add_sitemap")?.scopes).toEqual(["https://www.googleapis.com/auth/webmasters"])
  })

  it.each(connectors.filter((c) => !((c.auth.kind === "oauth2_user" || c.auth.kind === "oauth2_jwt_bearer") && c.auth.accessLevels)).map((c) => [c.name, c] as const))(
    "%s: without access levels, no function declares scopes",
    (_name, connector) => {
      for (const fn of connector.functions) expect(fn.scopes, fn.name).toBeUndefined()
    },
  )

  it.each(functions)("%s: act is resolved, never false when sensitive nor true when read", (_name, _connector, fn) => {
    expect(typeof fn.act).toBe("boolean")
    if (fn.class === "sensitive") expect(fn.act).toBe(true)
    if (fn.class === "read") expect(fn.act).toBe(false)
  })

  it.each(connectors.map((c) => [c.name, c] as const))("%s: every credential field says who holds it", (_name, connector) => {
    for (const field of connector.credential) expect(["account", "host"], field.name).toContain(field.holder)
  })

  // Compte rattaché : l'identité n'est citée que par l'adresse de base, après l'hôte, et aucune fonction ne la prend.
  it.each(connectors.flatMap((c) => (c.accountIdentity ? [[c.name, c, c.accountIdentity] as const] : [])))(
    "%s: the account identity is cited after the host only, and by no function",
    (_name, connector, identity) => {
      expect(identity.pattern).toMatch(/^\^.+\$$/)
      const cited = `{${identity.name}}`
      const urls = connector.baseUrl !== undefined ? [connector.baseUrl] : Object.values(connector.baseUrls?.values ?? {})
      expect(urls.length).toBeGreaterThan(0)
      for (const url of urls) expect(url).toMatch(new RegExp(`^(https://[^/{}]+|\\{[a-z][a-z0-9_]*\\})/.*\\{${identity.name}\\}`))
      for (const fn of connector.functions) {
        expect(Object.keys(fn.schema.properties as object), fn.name).not.toContain(identity.name)
        expect(fn.request.path, fn.name).not.toContain(cited)
        const body = typeof fn.request.body === "string" ? [] : bodyKeys(fn.request.body)
        for (const names of [Object.keys(fn.request.query), body, Object.keys(fn.request.headers)]) expect(names).not.toContain(identity.name)
      }
    },
  )

  // Signal de refus du service : un 2xx n'est un refus que sous une condition sur le corps ; un 403 seul ne dit jamais une
  // clé refusée, ni un 429 seul des crédits épuisés ; ce qui marque le compte ne se rejoue pas.
  it.each(connectors.map((c) => [c.name, c] as const))("%s: what marks the account is declared under a status, with a body condition where it must", (_name, connector) => {
    const entries = [
      ...connector.errors.map((e) => ({ statuses: typeof e.status === "number" ? [e.status] : e.status, body: e.body, mark: e.marksAccount, retryable: e.retryable })),
      ...connector.functions.flatMap((fn) =>
        fn.refusals.flatMap((r) => (typeof r.when === "number" ? [{ statuses: [r.when], body: r.body, mark: r.marksAccount, retryable: false }] : [])),
      ),
    ]
    for (const { statuses, body, mark, retryable } of entries) {
      if (statuses.some((status) => status >= 200 && status < 300)) expect(body).toBeDefined()
      if (mark === "credential_rejected" && body === undefined) expect(statuses).not.toContain(403)
      if (mark === "quota_exhausted" && body === undefined) expect(statuses).not.toContain(429)
      if (mark !== undefined) expect(retryable).toBe(false)
      if (body !== undefined) expect((body.equals !== undefined ? body.equals : body.contains).length).toBeGreaterThan(0)
    }
    for (const fn of connector.functions) {
      for (const r of fn.refusals) if (typeof r.when === "string") expect(r.body ?? r.marksAccount, `${fn.name}: ${r.code}`).toBeUndefined()
    }
  })

  it.each(connectors.filter((c) => c.baseUrls).map((c) => [c.name, c] as const))("%s: base URLs give one address per choice", (_name, connector) => {
    const setting = connector.settings?.find((s) => s.name === connector.baseUrls?.setting)
    expect(setting?.type).toBe("choice")
    if (setting?.type !== "choice") return
    expect(Object.keys(connector.baseUrls?.values ?? {}).sort()).toEqual([...setting.choices].sort())
    if (setting.default !== undefined) expect(setting.choices).toContain(setting.default)
    expect(connector.baseUrl).toBeUndefined()
  })

  const asyncJobs = functions.flatMap(([name, connector, fn]) => (fn.async ? [[name, connector, fn.async] as const] : []))
  it.each(asyncJobs)("%s: the job is collected by a read function of the connector whose only required argument is the job id", (name, connector, job) => {
    const collect = connector.functions.find((fn) => fn.name === job.collect)
    expect(collect?.class).toBe("read")
    expect(collect?.name).not.toBe(name)
    expect(collect?.schema.required).toEqual([job.jobId.to])
  })

  const uploads = functions.flatMap(([name, , fn]) => (fn.request.files ? [[name, fn, fn.request.files] as const] : []))
  it("should declare at least one function that sends a file", () => {
    expect(uploads.map(([name]) => name)).toContain("pennylane_firm.upload_dms_file")
  })

  it.each(uploads)("%s: each file part is a text argument, sent once in a body", (_name, fn, files) => {
    expect(["POST", "PUT", "PATCH"]).toContain(fn.request.method)
    expect(fn.pagination).toBeUndefined()
    const properties = fn.schema.properties as Record<string, { type?: unknown }>
    for (const [part, argument] of Object.entries(files)) {
      expect(properties[argument]?.type).toBe("string")
      expect(Object.keys(fn.request.body)).not.toContain(part)
      expect(fn.request.encode?.[argument]).toBeUndefined()
    }
  })

  it("should give each item cost two required list arguments", () => {
    for (const [name, , fn] of functions) {
      if (fn.cost?.per !== "item") continue
      const lists = [fn.cost.items, ...(fn.cost.times === undefined ? [] : [fn.cost.times])]
      for (const argument of lists) expect(fn.schema.required, name).toContain(argument)
    }
  })

  it("should weigh every allowed value of a weighted cost, and nothing else", () => {
    const weighted = functions.filter(([, , fn]) => fn.cost?.per === "item" && fn.cost.weights !== undefined)
    expect(weighted.length).toBeGreaterThan(0)
    for (const [name, , fn] of weighted) {
      if (fn.cost?.per !== "item" || fn.cost.weights === undefined) continue
      const properties = fn.schema.properties as Record<string, { items?: { required?: string[]; properties?: Record<string, { items?: { enum?: string[] } }> } }>
      const element = properties[fn.cost.items]?.items
      expect(element?.required, name).toContain(fn.cost.weights.field)
      const allowed = element?.properties?.[fn.cost.weights.field]?.items?.enum ?? []
      expect([...allowed].sort(), name).toEqual(Object.keys(fn.cost.weights.values).sort())
      for (const weight of Object.values(fn.cost.weights.values)) expect(Number.isInteger(weight) && weight >= 1, name).toBe(true)
    }
  })
})
