// JSON Schema for strict registry-backed connector templates.
export const templateSchema = {
  type: 'object',
  required: ['name', 'category', 'version', 'input', 'connector', 'output'],
  additionalProperties: false,
  properties: {
    name: { type: 'string', minLength: 1, description: 'Template name' },
    description: { type: 'string', description: 'Template description' },
    category: { type: 'string', minLength: 1, description: 'Template category' },
    version: { type: 'number', description: 'Template version' },
    execution_mode: {
      type: 'string',
      enum: ['preview'],
      default: 'preview',
      description: 'Connector templates do not write the graph directly'
    },
    input: {
      type: 'object',
      required: ['type'],
      additionalProperties: false,
      properties: {
        type: { type: 'string', description: 'Flowsint input type' },
        key: { type: 'string', default: 'nodeLabel', description: 'Input key' }
      }
    },
    connector: {
      type: 'object',
      required: ['destination_id', 'endpoint_id', 'capability'],
      additionalProperties: false,
      properties: {
        destination_id: {
          type: 'string',
          pattern: '^[a-z][a-z0-9_-]{0,127}$',
          description: 'Deployment-approved destination identifier'
        },
        endpoint_id: {
          type: 'string',
          pattern: '^[a-z][a-z0-9_-]{0,127}$',
          description: 'Deployment-approved endpoint identifier'
        },
        capability: {
          type: 'string',
          enum: ['enrich.read'],
          description: 'Template egress capability'
        }
      }
    },
    output: {
      type: 'object',
      required: ['type'],
      additionalProperties: false,
      properties: {
        type: { type: 'string', description: 'Flowsint output type' }
      }
    },
    projection: {
      type: 'object',
      required: ['profile_id', 'revision'],
      additionalProperties: false,
      properties: {
        profile_id: { type: 'string', pattern: '^[a-z][a-z0-9_]{0,63}$' },
        revision: { type: 'integer', minimum: 1 }
      }
    },

    evidence: {
      type: 'object',
      additionalProperties: false,
      properties: {
        source_rights: { type: 'string', default: 'unspecified' },
        schema_version: { type: 'string', default: '1' },
        parser_version: { type: 'string', default: '1' },
        confidence: { type: 'number', minimum: 0, maximum: 1, default: 1 },
        verification_state: { type: 'string', default: 'unverified' }
      }
    }
  }
}

export const defaultTemplate = `# Connector destinations and endpoint behavior are deployment-owned.
# Select only an approved destination_id and endpoint_id.
name: approved-enrichment
description: Preview a policy-approved enrichment endpoint
category: Location
version: 1.0
execution_mode: preview

input:
  type: Location
  key: address

connector:
  destination_id: approved_destination
  endpoint_id: approved_endpoint
  capability: enrich.read

output:
  type: Location

evidence:
  source_rights: unspecified
  schema_version: "1"
  parser_version: "1"
  confidence: 1.0
  verification_state: unverified
`

export interface TemplateInput {
  type: string
  key?: string
}

export interface TemplateConnector {
  destination_id: string
  endpoint_id: string
  capability: 'enrich.read'
}

export interface TemplateProjection {
  profile_id: string
  revision: number
}

export interface TemplateOutput {
  type: string
}

export interface TemplateEvidence {
  source_rights?: string
  schema_version?: string
  parser_version?: string
  confidence?: number
  verification_state?: string
}

export interface TemplateData {
  name: string
  description?: string
  category: string
  version: number
  execution_mode?: 'preview'
  input: TemplateInput
  connector: TemplateConnector
  output: TemplateOutput
  evidence?: TemplateEvidence
  projection?: TemplateProjection
}
