import { fetchWithAuth } from './api'
import type { TemplateData } from '@/components/templates/template-schema'

export interface Template {
  id: string
  name: string
  category: string
  version: number
  content: TemplateData
  is_public: boolean
  owner_id: string
  created_at: string
  updated_at: string
  description: string
}

export interface CreateTemplatePayload {
  name: string
  description?: string
  category: string
  version?: number
  content: TemplateData
  is_public?: boolean
}

export interface UpdateTemplatePayload {
  name?: string
  description?: string
  category?: string
  version?: number
  content?: TemplateData
  is_public?: boolean
}

export interface ConnectorTestOutcome {
  status: 'success' | 'failure' | 'hold'
  visible_outputs: number
  diagnostic?: {
    code: string
    safe_message: string
    retryable: boolean
  } | null
  evidence: Array<{
    destination_id: string
    endpoint_id: string
    capability: 'enrich.read'
    policy_version: string
    artifact_sha256?: string | null
    artifact_reference?: string | null
  }>
}

export interface TestTemplateResponse {
  success: boolean
  destination_id: string
  endpoint_id: string
  capability: 'enrich.read'
  outcomes: ConnectorTestOutcome[]
}

export interface GenerateTemplateResponse {
  yaml_content: string
}

export const templateService = {
  getAll: async (): Promise<Template[]> => {
    return fetchWithAuth('/api/enrichers/templates', {
      method: 'GET'
    })
  },

  getById: async (templateId: string): Promise<Template> => {
    return fetchWithAuth(`/api/enrichers/templates/${templateId}`, {
      method: 'GET'
    })
  },

  create: async (payload: CreateTemplatePayload): Promise<Template> => {
    return fetchWithAuth('/api/enrichers/templates', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  },

  update: async (templateId: string, payload: UpdateTemplatePayload): Promise<Template> => {
    return fetchWithAuth(`/api/enrichers/templates/${templateId}`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    })
  },

  delete: async (templateId: string): Promise<void> => {
    return fetchWithAuth(`/api/enrichers/templates/${templateId}`, {
      method: 'DELETE'
    })
  },

  test: async (templateId: string, inputValue: string): Promise<TestTemplateResponse> => {
    return fetchWithAuth(`/api/enrichers/templates/${templateId}/test`, {
      method: 'POST',
      body: JSON.stringify({ input_value: inputValue })
    })
  },


  generate: async (
    prompt: string,
    inputType?: string,
    outputType?: string
  ): Promise<GenerateTemplateResponse> => {
    return fetchWithAuth('/api/enrichers/templates/generate', {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        input_type: inputType || null,
        output_type: outputType || null
      })
    })
  }
}
