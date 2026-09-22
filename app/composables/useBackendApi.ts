import type { Auth } from 'firebase/auth'

type BackendRequestOptions = Omit<RequestInit, 'body' | 'headers'> & {
  body?: BodyInit | null
  headers?: HeadersInit
}

const LOCAL_BACKEND_PORTS = [8001, 8000, 8003, 8002]

function trimTrailingSlash(value: string) {
  return value.replace(/\/$/, '')
}

export function useBackendApi() {
  const config = useRuntimeConfig()
  const backendUrl = useState<string>('did-backend-url', () => '')

  async function resolveBackendUrl() {
    if (backendUrl.value) return backendUrl.value

    const configured = typeof config.public.backendUrl === 'string' ? config.public.backendUrl : ''
    const candidates = configured ? [configured] : LOCAL_BACKEND_PORTS.map((port) => `http://127.0.0.1:${port}`)

    for (const candidate of candidates) {
      const controller = new AbortController()
      const timeout = window.setTimeout(() => controller.abort(), 1500)
      try {
        const response = await fetch(`${trimTrailingSlash(candidate)}/health`, { signal: controller.signal })
        if (response.ok) {
          backendUrl.value = trimTrailingSlash(candidate)
          return backendUrl.value
        }
      } catch {
        // Try the next local backend port.
      } finally {
        window.clearTimeout(timeout)
      }
    }

    throw new Error('백엔드를 실행하세요: python -m backend.app')
  }

  async function request<T>(path: string, options: BackendRequestOptions = {}): Promise<T> {
    const { $auth, $authReady } = useNuxtApp()
    await $authReady
    const token = await ($auth as Auth).currentUser?.getIdToken()
    const response = await fetch(`${await resolveBackendUrl()}${path}`, {
      ...options,
      headers: {
        Accept: 'application/json',
        ...options.headers,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    if (!response.ok) {
      const body = await response.text()
      throw new Error(body || `백엔드 요청에 실패했습니다. (${response.status})`)
    }
    return await response.json() as T
  }

  return { request }
}
