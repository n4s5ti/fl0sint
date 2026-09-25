import { fetchWithAuth } from './api'

export interface Grievance {
  id: string
  created_at: string
  install_id: string
  tool_name: string
  report: string
}

export const grievanceService = {
  list: async (): Promise<Grievance[]> => {
    return fetchWithAuth('/v1/grievances', {
      method: 'GET'
    })
  },
  create: async (body: { install_id: string; tool_name: string; report: string }): Promise<Grievance> => {
    return fetchWithAuth('/v1/grievances', {
      method: 'POST',
      body: JSON.stringify(body)
    })
  }
}
