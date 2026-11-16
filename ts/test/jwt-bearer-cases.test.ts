// Les cas communs du compte de service (`src/jwt-bearer-cases.ts`, sortis par la fabrique de
// `connectors/_cases/jwt_bearer.yaml`) : des données que le paquet de l'hôte joue contre son moteur. Ici, leur forme, et
// une référence qui les rejoue par WebCrypto : l'assertion exacte (signature RS256 comprise), la requête de jeton exacte,
// ce que donne la réponse, et le refus de chaque clé refusée.
import { describe, expect, it } from "vitest"
import { credentialValue, jwtBearerCases, jwtBearerRefusedKeys, levelScopes, testKeyPkcs8 } from "../src/jwt-bearer-cases"
import type { Auth } from "../src/types"

type JwtBearer = Extract<Auth, { kind: "oauth2_jwt_bearer" }>

const PEM_HEAD = `-----BEGIN ${"PRIVATE KEY"}-----\n`
const PEM_TAIL = `-----END ${"PRIVATE KEY"}-----\n`

class Refused extends Error {}

function jwtBearer(auth: Auth): JwtBearer {
  if (auth.kind !== "oauth2_jwt_bearer") throw new Error(`not a service account: ${auth.kind}`)
  return auth
}

function bytesOf(base64: string): Uint8Array {
  return Uint8Array.from(atob(base64.replace(/\s/g, "")), (c) => c.charCodeAt(0))
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

const segment = (value: object): string => base64url(new TextEncoder().encode(JSON.stringify(value)))

/** La référence : la clé lue, ou son refus, jugé dans cet ordre. */
async function readKey(auth: JwtBearer, text: string): Promise<{ key: Record<string, unknown>; signer: CryptoKey }> {
  let key: unknown
  try {
    key = JSON.parse(text)
  } catch {
    throw new Refused("service_account_key_not_json")
  }
  if (typeof key !== "object" || key === null || Array.isArray(key)) throw new Refused("service_account_key_not_json")
  const file = key as Record<string, unknown>
  const filled = (field: string) => typeof file[field] === "string" && file[field] !== ""
  if (!filled("client_email") || !filled("private_key")) throw new Refused("service_account_key_incomplete")
  if ("token_uri" in file && file.token_uri !== auth.tokenUrl) throw new Refused("service_account_key_foreign_token_uri")
  const pem = file.private_key as string
  if (!pem.startsWith(PEM_HEAD) || !pem.endsWith(PEM_TAIL)) throw new Refused("service_account_key_unreadable")
  try {
    const der = bytesOf(pem.slice(PEM_HEAD.length, pem.length - PEM_TAIL.length))
    const signer = await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"])
    return { key: file, signer }
  } catch {
    throw new Refused("service_account_key_unreadable")
  }
}

/** La référence : les droits de l'assertion, `scopes`, ou ceux du niveau du compte (donné, sinon par défaut, sinon `full`). */
function claimed(auth: JwtBearer, accessLevel?: string): readonly string[] {
  if (auth.accessLevels === undefined) return auth.scopes
  const level = auth.accessLevels[accessLevel ?? auth.defaultLevel ?? "full"]
  if (level === undefined) throw new Error("access level not declared")
  return level.scopes
}

/** La référence : l'en-tête, les revendications et l'assertion signée. */
async function assertion(auth: JwtBearer, key: Record<string, unknown>, signer: CryptoKey, now: number, scopes: readonly string[]) {
  const header: Record<string, string> = { alg: "RS256", typ: "JWT" }
  if (typeof key.private_key_id === "string" && key.private_key_id !== "") header.kid = key.private_key_id
  const claims = { iss: key.client_email as string, scope: scopes.join(" "), aud: auth.tokenUrl, iat: now, exp: now + 3600 }
  const input = `${segment(header)}.${segment(claims)}`
  const signature = base64url(new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", signer, new TextEncoder().encode(input))))
  return { header, claims, signature, jwt: `${input}.${signature}` }
}

/** La référence : une redirection n'est jamais suivie ; `invalid_grant`, la clé refusée ; sinon le jeton et son échéance. */
function outcome(answer: { readonly status: number; readonly body: unknown }, now: number) {
  if (answer.status >= 300 && answer.status < 400) return { refusal: "upstream_error" }
  const body = answer.body as Record<string, unknown> | null
  if (answer.status >= 400) return { refusal: body?.error === "invalid_grant" ? "credential_rejected" : "upstream_error" }
  return { headers: { authorization: `Bearer ${body?.access_token as string}` }, expiresAt: now + Number(body?.expires_in) }
}

describe("jwt bearer cases", () => {
  it("should carry the test key as a PKCS#8 body, never framed", () => {
    expect(testKeyPkcs8).toMatch(/^([A-Za-z0-9+/=]{1,64}\n)+$/)
  })

  it.each(jwtBearerCases.map((c) => [c.name, c] as const))("%s: the exact assertion, token request and outcome", async (_name, c) => {
    const auth = jwtBearer(c.auth)
    expect(c.credential.map((f) => f.name)).toEqual([auth.key])
    const { key, signer } = await readKey(auth, credentialValue(c))
    expect(levelScopes(c.auth, c.accessLevel)).toEqual(claimed(auth, c.accessLevel))
    const signed = await assertion(auth, key, signer, c.now, claimed(auth, c.accessLevel))
    expect([Object.entries(signed.header), Object.entries(signed.claims), signed.signature]).toEqual([
      Object.entries(c.assertion.header),
      Object.entries(c.assertion.claims),
      c.assertion.signature,
    ])
    const body = new URLSearchParams([["grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer"], ["assertion", signed.jwt]]).toString()
    expect(c.tokenRequest.url).toBe(auth.tokenUrl)
    expect(body).toBe(c.tokenRequest.body.replace("{assertion}", signed.jwt))
    expect(outcome(c.tokenAnswer, c.now)).toEqual(c.outcome)
  })

  it("should cover a level given and the default level of a service account with access levels", () => {
    const leveled = jwtBearerCases.filter((c) => jwtBearer(c.auth).accessLevels !== undefined)
    expect(new Set(leveled.map((c) => c.accessLevel !== undefined))).toEqual(new Set([true, false]))
    const first = leveled[0]
    if (first === undefined) throw new Error("no case with access levels")
    expect(() => levelScopes(first.auth, "admin")).toThrow("access level not declared: admin")
  })

  it.each(jwtBearerRefusedKeys.map((c) => [c.name, c] as const))("%s: refused before anything is sent", async (_name, c) => {
    await expect(readKey(jwtBearer(c.auth), credentialValue(c))).rejects.toThrow(new Refused(c.refusal))
  })
})
