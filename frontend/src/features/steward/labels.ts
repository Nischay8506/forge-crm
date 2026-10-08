import type { Band } from "./types"

export const FIELD_LABELS: Record<string, string> = {
  kind: "Type", site_type: "Site type", parent_id: "Belongs to", name: "Name",
  legal_name: "Legal name", gstin: "GSTIN", pan: "PAN", cin: "CIN", website: "Website",
  domain: "Email domain", email: "Email", phone: "Phone", address_line1: "Address",
  address_line2: "Address line 2", city: "City", state: "State", pincode: "Pincode",
  country: "Country", industry: "Industry", account_id: "Company", full_name: "Name",
  designation: "Designation", department: "Department", preferred_language: "Preferred language",
}

export const fieldLabel = (f: string) => FIELD_LABELS[f] ?? f

export const KIND_LABELS: Record<string, string> = {
  group: "Group", entity: "Legal entity", site: "Site",
}

export const SITE_TYPE_LABELS: Record<string, string> = {
  plant: "Plant", ship_to: "Ship-to", bill_to: "Bill-to", office: "Office",
  warehouse: "Warehouse", branch: "Branch (GST registration)",
}

export const SOURCE_LABELS: Record<string, string> = {
  registry: "Registry", erp: "ERP", manual: "Entered by a person", document: "Document",
  import: "Import", merge: "Merge", signature: "Email signature", agent: "Agent",
}

export const BAND_LABELS: Record<Band, string> = {
  auto_merge: "Same record", review: "Needs review", distinct: "Different",
}

/** Tag colour per reason effect from app/steward/matching.py */
export function effectTone(effect: string): "green" | "teal" | "amber" | "red" | "neutral" {
  if (effect === "strong") return "green"
  if (effect === "medium") return "teal"
  if (effect === "distinct") return "red"
  if (effect === "weak-negative") return "amber"
  return "neutral"
}

export function isAgent(actor: string | null | undefined): boolean {
  return !!actor && actor.startsWith("agent:")
}
