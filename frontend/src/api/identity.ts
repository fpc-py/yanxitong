// 浏览器端身份标识的持久化：匿名 id（未登录配额跟踪）与登录 token
// axios 拦截器与 auth store 共用这一份读写，避免互相依赖

const ANON_KEY = 'yxt.anon_id'
const TOKEN_KEY = 'yxt.token'

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* 隐私模式等场景静默忽略 */
  }
}

function randomId(): string {
  // randomUUID 仅安全上下文可用（localhost 满足）；否则退回随机串
  const c = globalThis.crypto as Crypto | undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `anon-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

let cachedAnonId: string | null = null

/** 浏览器级匿名 id：首次访问生成并持久化，之后保持不变（后端据此统计免费次数） */
export function getAnonId(): string {
  if (cachedAnonId) return cachedAnonId
  let id = safeGet(ANON_KEY)
  if (!id) {
    id = randomId()
    safeSet(ANON_KEY, id)
  }
  cachedAnonId = id
  return id
}

export function getToken(): string | null {
  return safeGet(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  safeSet(TOKEN_KEY, token)
}
