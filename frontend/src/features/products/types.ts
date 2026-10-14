/** Shapes of the product configurator API (F3.1).
 *  Mirrors backend app/api/v1/products.py and app/cpq/configurator.py. */

export type RuleKind = "requires" | "excludes" | "only_when"

export interface OptionDef { value: string; label: string; code: string; erp_item?: string }

export interface AttributeDef {
  key: string
  label: string
  kind: "choice" | "number"
  required: boolean
  options?: OptionDef[]
  unit?: string | null
  min?: string | number | null
  max?: string | number | null
  step?: string | number | null
}

/** A condition: an attribute is one of some options, or a number within min and max. */
export interface Cond { attr: string; in?: string[]; min?: string | number; max?: string | number }

export interface RuleDef { id?: string; kind: RuleKind; when: Cond[]; then: Cond; message: string }

export interface OptionsModel { attributes: AttributeDef[]; rules: RuleDef[]; code_pattern: string }

export interface SavedModel extends OptionsModel {
  id: string
  version: number
  note: string | null
  created_by: string
  created_at: string
}

export interface FamilyOptions {
  family: { id: string; name: string; make_ability: string }
  options: SavedModel | null
  versions: { version: number; note: string | null; created_by: string; created_at: string }[]
  samples: string[]
}

export interface GuidedChoice { value: string; label: string; code: string; available: boolean; why?: string }

export interface GuidedRange { min: string; max: string; available: boolean; why?: string }

export interface GuidedAttribute {
  key: string
  label: string
  kind: "choice" | "number"
  required: boolean
  chosen: string | null
  options?: GuidedChoice[]
  unit?: string | null
  min?: string
  max?: string
  step?: string
  ranges?: GuidedRange[]
}

/** One picked attribute, for showing a configuration: "Pressure class: Class 300". */
export interface PickedLabel { key: string; label: string; value: string; text: string }

export interface GuidedState {
  family: { id: string; name: string }
  version: number
  attributes: GuidedAttribute[]
  complete: boolean
  valid: boolean
  problems: string[]
  result: { values: Record<string, string>; labels: PickedLabel[]; variant_code: string; fingerprint: string } | null
}

export interface Configuration {
  id: string
  family_id: string
  model_id: string
  values: Record<string, string>
  labels: PickedLabel[]
  variant_code: string
  fingerprint: string
  reprice_reason: string | null
  reprice_at: string | null
  created_by: string
  created_at: string
}
