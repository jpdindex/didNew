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
    const user = ($auth as Auth).currentUser
    const token = await user?.getIdToken()
    const response = await fetch(`${await resolveBackendUrl()}${path}`, {
      ...options,
      headers: {
        Accept: 'application/json',
        ...options.headers,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        // Local uvicorn accepts this only from its configured loopback frontend
        // origins. It keeps two locally signed-in analysts distinct even when
        // Admin token verification is not configured on the development host.
        ...(user?.uid ? { 'X-Did-Local-User': user.uid } : {}),
      },
    })
    if (!response.ok) {
      const body = await response.text()
      throw new Error(body || `백엔드 요청에 실패했습니다. (${response.status})`)
    }
    return await response.json() as T
  }

  async function openSocket<T>(
    path: string,
    onMessage: (data: T) => void,
    onClose?: () => void,
  ): Promise<{ close: () => void; send: (data: unknown) => void }> {
    const { $auth, $authReady } = useNuxtApp()
    await $authReady
    const user = ($auth as Auth).currentUser
    const token = await user?.getIdToken()
    const url = (await resolveBackendUrl()).replace(/^http/, 'ws') + path
    return await new Promise((resolve, reject) => {
      const socket = new WebSocket(url)
      let ready = false
      let settled = false
      const failBeforeReady = (error: Error) => {
        if (settled || ready) return
        settled = true
        window.clearTimeout(timeout)
        reject(error)
      }
      const timeout = window.setTimeout(() => {
        if (!ready && !settled) {
          socket.close()
          failBeforeReady(new Error('실시간 협업 연결 시간이 초과되었습니다.'))
        }
      }, 3_000)
      socket.onopen = () => socket.send(JSON.stringify({ type: 'authenticate', token, uid: user?.uid }))
      socket.onerror = () => failBeforeReady(new Error('실시간 협업 연결에 실패했습니다.'))
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(String(event.data)) as T & { type?: string }
          if (data.type === 'ready') {
            ready = true
            settled = true
            window.clearTimeout(timeout)
            resolve({
              // The collaboration owner marks an intentional close as stopped
              // before calling this. Keep onclose installed so a timed-out
              // command can close a stale socket and trigger reconnection.
              close: () => socket.close(),
              send: (value) => {
                if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value))
              },
            })
            return
          }
          onMessage(data)
        } catch {
          // Invalid transport frames must not interrupt the input UI.
        }
      }
      socket.onclose = () => {
        window.clearTimeout(timeout)
        if (!ready) failBeforeReady(new Error('실시간 협업 연결이 닫혔습니다.'))
        else onClose?.()
      }
    })
  }

  return { request, openSocket }
}
