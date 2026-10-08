import { describe, expect, it } from 'vitest'
import { hasPermission } from './permissions'

describe('hasPermission', () => {
  it('grants with exact code or wildcard', () => {
    expect(hasPermission(['users.read'], 'users.read')).toBe(true)
    expect(hasPermission(['*'], 'users.manage')).toBe(true)
  })
  it('denies otherwise', () => {
    expect(hasPermission(['users.read'], 'users.manage')).toBe(false)
    expect(hasPermission([], 'users.read')).toBe(false)
  })
})
