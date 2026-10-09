import { describe, expect, it } from "vitest"
import { keyBytes } from "./push"

describe("keyBytes", () => {
  it("decodes a base64url key without padding", () => {
    // "-_8" is base64url for the bytes fb ff (the characters standard base64 writes as + and /).
    expect(Array.from(keyBytes("-_8"))).toEqual([0xfb, 0xff])
    expect(Array.from(keyBytes("AQID"))).toEqual([1, 2, 3])
  })
})
