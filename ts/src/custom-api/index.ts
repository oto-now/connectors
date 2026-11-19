// Custom API, le module TypeScript : une définition d'organisation est un `connector.yaml` du format, restreint par le
// profil (docs/conception/custom-api.md). `validate` la juge avec les mêmes codes `custom_api_*` que le paquet Python ;
// `toDefinition` la projette dans la définition que la fabrique génère pour un connecteur partagé, que l'hôte déclare
// comme les autres. L'import OpenAPI et la reprise d'oto 1 restent dans le paquet Python.
export { validate, PREFIX, MAX_FUNCTIONS, MAX_BYTES, AUTH_KINDS, type SchemaCompiler, type SchemaIssue } from "./profile"
export { toDefinition, NotGenerated } from "./definition"
export { CODES, type Problem, type ProblemCode } from "./problems"
