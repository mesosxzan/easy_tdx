const WENCAI_COOKIE_STORAGE_KEY = 'easy_tdx_wencai_cookie'

function getStorage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function getWencaiCookie(): string {
  const storage = getStorage()
  return storage?.getItem(WENCAI_COOKIE_STORAGE_KEY)?.trim() ?? ''
}

export function setWencaiCookie(cookie: string): void {
  const storage = getStorage()
  if (!storage) return
  const value = cookie.trim()
  if (value) {
    storage.setItem(WENCAI_COOKIE_STORAGE_KEY, value)
  } else {
    storage.removeItem(WENCAI_COOKIE_STORAGE_KEY)
  }
}

export { WENCAI_COOKIE_STORAGE_KEY }
