# @otomata_tech/connectors

Shared connector definitions for [Oto](https://github.com/otomata-tech/connectors): one definition per connector and per
function (input JSON Schema, HTTP request, authentication, errors, pagination, cost), generated from
`connectors/*/connector.yaml` by the repository's generator (the *fabrique*).

The package is structural data only. It sends no request and reads no secret: **the host runs the requests**, with its
own HTTP client, credential vault and logs, and passes each account's secret at call time.

## Install

Pin an exact version:

```bash
npm i @otomata_tech/connectors@<version>
```

The version is the repository's: each tag `vX.Y.Z` publishes this package and the Python library
([`oto-core`](https://pypi.org/project/oto-core/)) at the same version.

## Use

```ts
import { connectors, type Connector } from "@otomata_tech/connectors"

const notion: Connector | undefined = connectors.find((c) => c.name === "notion")
```

`connectors` lists every generated connector; each one carries its functions. The contract of a definition is
`src/types.ts`, also exported as `@otomata_tech/connectors/types`.

An organization's own API (Custom API) is a `connector.yaml` document restricted by a profile.
`@otomata_tech/connectors/custom-api` judges it and projects it into the same `Connector` shape; the package has no
dependency, so the host passes its JSON Schema (2020-12) validator, in Ajv's error shape:

```ts
import { Ajv2020 } from "ajv/dist/2020.js"
import { toDefinition, validate } from "@otomata_tech/connectors/custom-api"

const ajv = new Ajv2020({ strict: false, validateFormats: false, allErrors: true })
ajv.addKeyword({ keyword: "personal", schemaType: "boolean" })
const compile = (schema: object) => {
  const check = ajv.compile(schema)
  return (value: unknown) => (check(value) ? [] : check.errors ?? [])
}
const problems = validate(document, compile) // [] → saved; else each problem with its custom_api_* code
const connector = toDefinition(document)
```

The package ships its TypeScript sources (`src/*.ts`), not compiled JavaScript: the host transpiles them (with Next.js,
list the package in `transpilePackages`).

## License

MIT
