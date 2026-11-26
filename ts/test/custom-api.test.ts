/// <reference types="vite/client" />
// Le module Custom API du paquet (`src/custom-api/`). Les cas communs `connectors/_cases/custom_api.yaml`, joués ici comme
// côté Python (`tests/test_custom_api_cases.py`) : chaque définition valide passe le profil sans un problème, chaque
// variante refusée donne exactement ses codes et les règles du format nommées ; chaque document OpenAPI donne, par
// `fromOpenApi`, exactement ce que donne `from_openapi` côté Python. Puis la garde de la projection :
// `toDefinition`, appliquée à chaque `connectors/*/connector.yaml`, donne exactement la définition que la fabrique a
// générée dans `src/<nom>.ts`. Le validateur JSON Schema est Ajv, celui que l'hôte passerait.
import { describe, expect, it } from "vitest"
import { connectors, type Connector } from "../src"
import { CODES, NotGenerated, OpenAPIRefused, fromOpenApi, toDefinition, validate } from "../src/custom-api"
import { changed, compile, read } from "./schema-compiler"

const casesText = import.meta.glob("../../connectors/_cases/custom_api.yaml", { query: "?raw", import: "default", eager: true })
const descriptions = import.meta.glob("../../connectors/*/connector.yaml", { query: "?raw", import: "default", eager: true })

type Change = { path: string; value?: unknown; remove?: boolean }
type Refused = { name: string; base: string; changes: Change[]; codes: string[]; rules?: string[] }
type OpenApiCase = {
  name: string
  document: string
  refused?: string
  candidates: { name: string; problems: string[] }[]
  skipped: { method: string; path: string; code: string }[]
  connector_problems: string[]
  connector: unknown
  functions: unknown[]
}
const cases = read(Object.values(casesText)[0] as string) as {
  valid: { name: string; document: unknown }[]
  refused: Refused[]
  openapi: OpenApiCase[]
}
const valid = new Map(cases.valid.map((c) => [c.name, c.document]))

describe("custom api cases", () => {
  it.each(cases.valid.map((c) => [c.name, c.document] as const))("%s: valid", (_name, document) => {
    expect(validate(document, compile)).toEqual([])
  })

  it.each(cases.refused.map((c) => [c.name, c] as const))("%s: refused with its codes", (_name, c) => {
    const found = validate(changed(valid.get(c.base), c.changes), compile)
    expect([...new Set(found.map((p) => p.code))].sort(), JSON.stringify(found)).toEqual([...c.codes].sort())
    expect([...new Set(found.flatMap((p) => (p.rule ? [p.rule] : [])))].sort()).toEqual([...(c.rules ?? [])].sort())
    for (const p of found) expect(CODES).toContain(p.code)
  })

  it.each(cases.openapi.map((c) => [c.name, c] as const))("%s: the import gives what Python gives", (_name, c) => {
    if (c.refused !== undefined) {
      expect(() => fromOpenApi(c.document, { compile, yaml: read })).toThrow(OpenAPIRefused)
      try {
        fromOpenApi(c.document, { compile, yaml: read })
      } catch (refused) {
        expect((refused as OpenAPIRefused).code).toBe(c.refused)
      }
      return
    }
    const result = fromOpenApi(c.document, { compile, yaml: read })
    expect(result.candidates.map((x: any) => ({ name: x.function.name, problems: x.problems.map((p: any) => p.code) }))).toEqual(c.candidates)
    expect(result.skipped.map((s: any) => ({ method: s.method, path: s.path, code: s.code }))).toEqual(c.skipped)
    expect(result.problems.map((p: any) => p.code).sort()).toEqual([...c.connector_problems].sort())
    expect(result.connector).toStrictEqual(c.connector)
    expect(result.candidates.map((x: any) => x.function)).toStrictEqual(c.functions)
  })

  it.each(cases.openapi.filter((c) => c.refused === undefined && !c.connector_problems.length).map((c) => [c.name, c] as const))(
    "%s: the candidates without problem make a valid definition",
    (_name, c) => {
      const result = fromOpenApi(c.document, { compile, yaml: read })
      const document = { connector: result.connector, functions: result.candidates.filter((x: any) => !x.problems.length).map((x: any) => x.function) }
      expect(validate(document, compile)).toEqual([])
    },
  )

  it("should refuse a text that is not JSON when no YAML reader is given, never guess", () => {
    const yamlCase = cases.openapi.find((c) => c.name === "openapi_3_yaml") as OpenApiCase
    expect(() => fromOpenApi(yamlCase.document, { compile })).toThrow("no YAML reader")
  })

  it("should refuse what is not an object, and an address that is not on the public internet", () => {
    expect(validate([], compile).map((p) => p.code)).toEqual(["custom_api_schema_invalid"])
    const base = valid.get("acme_tickets")
    for (const url of ["https://127.0.0.1/api", "https://[::1]/api", "https://[::ffff:10.0.0.1]/api", "https://[fd00::1]/api", "https://printer.local/api"])
      expect(validate(changed(base, [{ path: "connector.base_url", value: url }]), compile).map((p) => p.code), url).toEqual([
        "custom_api_base_url_internal",
      ])
    expect(validate(changed(base, [{ path: "connector.base_url", value: "https://8.8.8.8/api" }]), compile)).toEqual([])
  })

  it("should project a valid definition, its functions qualified", () => {
    const definition = toDefinition(valid.get("acme_tickets"))
    expect(definition.functions.map((fn) => fn.name)).toEqual(definition.functions.map((fn) => `${definition.name}.${fn.name.split(".")[1]}`))
  })

  it("should project the description and the logo domain in camel case", () => {
    const definition = toDefinition(valid.get("acme_planning"))
    expect([definition.description, definition.logoDomain]).toEqual([
      "Shifts of the teams of an organisation, read and planned.",
      "acme-planning.test",
    ])
    expect("logoDomain" in toDefinition(valid.get("acme_tickets"))).toBe(false)
  })
})

describe("projection guard", () => {
  const generated = new Map(connectors.map((connector) => [connector.name, connector]))
  const documents = Object.entries(descriptions).map(([path, text]) => [path.split("/").at(-2) as string, read(text as string)] as const)

  it("should cover every description of the fabrique", () => {
    expect(documents.map(([name]) => name).sort()).toEqual([...generated.keys()].sort())
  })

  // `logo` vient du fichier `logo.png` à côté de la description, pas du document : la projection ne peut pas le donner.
  it.each(documents)("%s: toDefinition gives the module the fabrique generated, but its logo", (name, document) => {
    const { logo: _logo, ...withoutLogo } = generated.get(name) as Connector
    expect(toDefinition(document)).toStrictEqual(withoutLogo)
  })

  it("should raise what the fabrique does not generate, never drop it", () => {
    const document = structuredClone(documents[0]?.[1]) as any
    document.functions[0].input.additionalProperties = true
    expect(() => toDefinition(document)).toThrow(NotGenerated)
  })
})
