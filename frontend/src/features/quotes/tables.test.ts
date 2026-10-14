import { describe, expect, it } from "vitest"
import { numberText, parsePasted, tableText } from "./tables"

describe("tables pasted from Excel (K8 A)", () => {
  it("reads a grid with the second key across the top", () => {
    const { table, problems } = parsePasted("size\\class\t150\t300\n15\t0.9\t0.9\n150\t10.9\t19.1\n600\t124\t", ["size", "class"], "grid")
    expect(problems).toEqual([])
    expect(table.rows).toEqual([
      { key: ["15", "150"], value: "0.9" }, { key: ["15", "300"], value: "0.9" },
      { key: ["150", "150"], value: "10.9" }, { key: ["150", "300"], value: "19.1" },
      { key: ["600", "150"], value: "124" }])
  })

  it("reads one row per entry, skipping a header row", () => {
    const { table, problems } = parsePasted("material\tvalue\nF316\t₹ 1,410\nA105\t95.50\nLF2\theavy", ["material"], "list")
    expect(table.rows).toEqual([{ key: ["F316"], value: "1410" }, { key: ["A105"], value: "95.50" }])
    expect(problems).toEqual(['Row 4: "heavy" is not a number'])
  })

  it("says what is wrong", () => {
    expect(parsePasted("15\t300", ["size", "class"], "list").problems).toEqual(["Row 1: needs 3 cells (size, class, value)"])
    expect(parsePasted("a\tb\n1\tx", ["size", "class"], "grid").problems).toEqual(['Row 2, class b: "x" is not a number'])
    expect(parsePasted("1\t2", ["size"], "grid").problems).toEqual(["The grid layout is for tables with two keys"])
  })

  it("writes the table back in either layout", () => {
    const t = { keys: ["size", "class"], rows: [{ key: ["15", "150"], value: "0.9" }, { key: ["15", "300"], value: "1.4" }, { key: ["20", "300"], value: "1.8" }] }
    expect(tableText(t, "grid")).toBe("size\\class\t150\t300\n15\t0.9\t1.4\n20\t\t1.8")
    expect(parsePasted(tableText(t, "grid"), t.keys, "grid").table).toEqual(t)
    expect(parsePasted(tableText(t, "list"), t.keys, "list").table).toEqual(t)
  })

  it("reads numbers the Indian way", () => {
    expect(numberText("1,23,456.50")).toBe("123456.50")
    expect(numberText("12a")).toBeNull()
  })
})
