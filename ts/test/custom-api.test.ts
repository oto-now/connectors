/// <reference types="vite/client" />
// Le module Custom API du paquet (`src/custom-api/`). Les cas communs `connectors/_cases/custom_api.yaml`, joués ici comme
// côté Python (`tests/test_custom_api_cases.py`) : chaque définition valide passe le profil sans un problème, chaque
// variante refusée donne exactement ses codes et les règles du format nommées. Puis la garde de la projection :
// `toDefinition`, appliquée à chaque `connectors/*/connector.yaml`, donne exactement la définition que la fabrique a
// générée dans `src/<nom>.ts`. Le validateur JSON Schema est Ajv, celui que l'hôte passerait.
import { Ajv2020, type ValidateFunction } from "ajv/dist/2020.js"
import { describe, expect, it } from "vitest"
import { parse } from "yaml"
import { connectors } from "../src"
import { CODES, NotGenerated, toDefinition, validate, type SchemaCompiler } from "../src/custom-api"
import type { JsonSchema } from "../src/types"

// Comme `yaml.safe_load` côté Python : clés de fusion, ancres sans plafond (les descriptions en usent) ; le schéma
// « core » de YAML 1.2, car celui de YAML 1.1 lirait `y` et `n` en booléens, ce que PyYAML ne fait pas.
const read = (text: string) => parse(text, { schema: "core", merge: true, maxAliasCount: -1 })

const casesText = import.meta.glob("../../connectors/_cases/custom_api.yaml", { query: "?raw", import: "default", eager: true })
const descriptions = import.meta.glob("../../connectors/*/connector.yaml", { query: "?raw", import: "default", eager: true })

type Change = { path: string; value?: unknown; remove?: boolean }
type Refused = { name: string; base: string; changes: Change[]; codes: string[]; rules?: string[] }
const cases = read(Object.values(casesText)[0] as string) as { valid: { name: string; document: unknown }[]; refused: Refused[] }
const valid = new Map(cases.valid.map((c) => [c.name, c.document]))

const ajv = new Ajv2020({ strict: false, validateFormats: false, allErrors: true })
ajv.addKeyword({ keyword: "personal", schemaType: "boolean" })
const compiled = new WeakMap<object, ValidateFunction>()
const compile: SchemaCompiler = (schema: JsonSchema) => {
  let check = compiled.get(schema)
  if (check === undefined) {
    check = ajv.compile(schema)
    compiled.set(schema, check)
  }
  const run = check
  return (value) => (run(value) ? [] : [...(run.errors ?? [])])
}

/** La définition après ses changements : chaque chemin à points, posé (`value`) ou retiré (`remove`). */
function changed(document: unknown, changes: readonly Change[]): unknown {
  const out = structuredClone(document)
  for (const change of changes) {
    const parts = change.path.split(".")
    const last = parts.pop() as string
    let node: any = out
    for (const part of parts) node = node[Array.isArray(node) ? Number(part) : part]
    const key = Array.isArray(node) ? Number(last) : last
    if (change.remove) {
      if (Array.isArray(node)) node.splice(key as number, 1)
      else delete node[key]
    } else node[key] = structuredClone(change.value)
  }
  return out
}

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

  it.each(documents)("%s: toDefinition gives the module the fabrique generated", (name, document) => {
    expect(toDefinition(document)).toStrictEqual(generated.get(name))
  })

  it("should raise what the fabrique does not generate, never drop it", () => {
    const document = structuredClone(documents[0]?.[1]) as any
    document.functions[0].input.additionalProperties = true
    expect(() => toDefinition(document)).toThrow(NotGenerated)
  })
})
