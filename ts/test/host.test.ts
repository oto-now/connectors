/// <reference types="vite/client" />
// Le profil hôte du paquet (`src/custom-api/host.ts`). Les cas communs `connectors/_cases/host.yaml`, joués ici comme
// côté Python (`tests/test_host_cases.py`) : chaque document valide donne sa définition, ses espaces de noms dans l'ordre,
// les métadonnées de l'hôte et chaque fonction du format en entier, chaque connecteur accepté par le schéma, les exemples
// et les règles du format ; chaque variante refusée ne donne aucune définition, et exactement ses problèmes, par code et
// par place. Puis la projection d'un connecteur de l'hôte par `toDefinition`. Le validateur JSON Schema est Ajv.
import { describe, expect, it } from "vitest"
import { fromHostOpenApi, HOST_CODES, toDefinition } from "../src/custom-api"
import { exampleProblems, ruleProblems, schemaProblems } from "../src/custom-api/profile"
import { changed, compile, read } from "./schema-compiler"

const casesText = import.meta.glob("../../connectors/_cases/host.yaml", { query: "?raw", import: "default", eager: true })

type Change = { path: string; value?: unknown; remove?: boolean }
type Valid = {
  name: string
  base: string
  changes: Change[]
  namespaces: string[]
  connector: { [key: string]: unknown }
  host?: unknown
  functions?: unknown
  served?: { [name: string]: unknown }
}
type Refused = { name: string; base?: string; changes?: Change[]; text?: string; problems: [string, string][]; rules?: string[] }
const cases = read(Object.values(casesText)[0] as string) as {
  reserved: string[]
  documents: { name: string; document: unknown }[]
  valid: Valid[]
  refused: Refused[]
}
const bases = new Map(cases.documents.map((c) => [c.name, c.document]))
const options = { compile, reserved: cases.reserved, yaml: read }
const documentOf = (c: { base?: string; changes?: Change[]; text?: string }) =>
  c.text !== undefined ? c.text : changed(bases.get(c.base as string), c.changes ?? [])

describe("host profile cases", () => {
  it.each(cases.valid.map((c) => [c.name, c] as const))("%s: valid", (_name, c) => {
    const result = fromHostOpenApi(documentOf(c), options)
    expect(result.problems).toEqual([])
    const documents = result.definition.connectors
    expect(documents.map((d: any) => d.connector.name)).toEqual(c.namespaces)
    for (const document of documents) {
      expect(Object.fromEntries(Object.keys(c.connector).map((k) => [k, document.connector[k]]))).toStrictEqual(c.connector)
      expect(schemaProblems(document, [], compile)).toEqual([[], true])
      expect(exampleProblems(document, compile)).toEqual([])
      expect(ruleProblems(document)).toEqual([])
    }
    if (c.host !== undefined) expect(result.definition.host).toStrictEqual(c.host)
    if (c.functions !== undefined) expect(result.definition.functions).toStrictEqual(c.functions)
    if (c.served !== undefined) {
      const served = Object.fromEntries(documents.flatMap((d: any) => d.functions.map((f: any) => [`${d.connector.name}.${f.name}`, f])))
      expect(served).toStrictEqual(c.served)
    }
  })

  it.each(cases.refused.map((c) => [c.name, c] as const))("%s: refused with its problems", (_name, c) => {
    const result = fromHostOpenApi(documentOf(c), options)
    expect(result.definition).toBeNull()
    const found = [...new Set(result.problems.map((p) => JSON.stringify([p.code, p.path])))].sort()
    expect(found, JSON.stringify(result.problems)).toEqual(c.problems.map((p) => JSON.stringify(p)).sort())
    expect([...new Set(result.problems.flatMap((p) => (p.rule ? [p.rule] : [])))].sort()).toEqual([...(c.rules ?? [])].sort())
  })

  it("should name each of its own codes in HOST_CODES", () => {
    const own = cases.refused.flatMap((c) => c.problems.map(([code]) => code)).filter((code) => code.startsWith("host_"))
    for (const code of own) expect(HOST_CODES).toContain(code)
  })

  it("should project a connector of the host, its functions named namespace.verb, confirmed by the host", () => {
    const result = fromHostOpenApi(bases.get("projects_and_shares"), options)
    const share = toDefinition(result.definition.connectors[1])
    expect(share.auth).toStrictEqual({ kind: "host_delegated", issuer: "https://auth.example.test/oidc", resource: "https://api.example.test" })
    expect(share.credential).toEqual([])
    expect(share.baseUrl).toBe("{address}/api/v2")
    expect(share.functions.map((fn) => [fn.name, fn.confirm])).toEqual([["share.create", { by: "host" }]])
  })
})
