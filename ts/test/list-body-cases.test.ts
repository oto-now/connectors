// Les cas communs du corps en liste (`src/list-body-cases.ts`, sortis par la fabrique de `connectors/_cases/list_body.yaml`) :
// des données que le paquet de l'hôte joue contre son moteur et `registerConnectors`. Ici, leur forme : chaque fonction
// des cas envoie son corps en liste ; chaque cas valide l'entrée et attend pour corps une liste d'un objet ; chaque
// entrée rejetée est refusée par le schéma ; chaque fonction refusée nomme l'un des trois refus de la règle 23.
import { Ajv2020 } from "ajv/dist/2020.js"
import { describe, expect, it } from "vitest"
import { connectors } from "../src"
import { connector, listBodyCases, listBodyRefusals, listBodyRefusedFunctions, listBodyRejectedInputs } from "../src/list-body-cases"
import type { ConnectorFunction } from "../src/types"

/** Les fonctions du connecteur des cas, sous le type du contrat (leurs clés facultatives lisibles). */
const caseFunctions: readonly ConnectorFunction[] = connector.functions

const ajv = new Ajv2020({ strictTypes: false, strictTuples: false, validateFormats: false })
ajv.addKeyword({ keyword: "personal", schemaType: "boolean" })

describe("list body cases", () => {
  it("should stay out of the index: test data, not a connector a host declares", () => {
    expect(connectors.map((c) => c.name)).not.toContain(connector.name)
  })

  it("should name the three refusals of rule 23, each with a refused function", () => {
    expect([...listBodyRefusals]).toEqual(["body_list_without_body", "body_list_method", "body_list_multipart"])
    expect(new Set(listBodyRefusedFunctions.map((r) => r.refusal))).toEqual(new Set(listBodyRefusals))
  })

  it.each(caseFunctions.map((fn) => [fn.name, fn] as const))("%s: a body sent as a list", (_name, fn) => {
    const validate = ajv.compile(fn.schema)
    for (const example of fn.examples) expect(validate(example.input), example.title).toBe(true)
    expect(fn.request.bodyList).toBe(true)
  })

  it.each(listBodyCases.map((c) => [c.name, c] as const))("%s: a valid input, a list of one object for body", (_name, c) => {
    expect(connector.functions).toContain(c.function)
    expect(ajv.compile(c.function.schema)(c.input)).toBe(true)
    expect(Array.isArray(c.request.body)).toBe(true)
    expect(c.request.body).toHaveLength(1)
  })

  it.each(listBodyRejectedInputs.map((r) => [r.name, r] as const))("%s: refused by the input schema", (_name, r) => {
    expect(connector.functions).toContain(r.function)
    expect(ajv.compile(r.function.schema)(r.input)).toBe(false)
  })

  it.each(listBodyRefusedFunctions.map((r) => [r.name, r] as const))("%s: outside the connector, flagged", (_name, r) => {
    expect(connector.functions).not.toContain(r.function)
    expect(r.function.request.bodyList).toBe(true)
    expect(listBodyRefusals).toContain(r.refusal)
  })
})
