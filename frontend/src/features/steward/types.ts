/** Shapes returned by backend app/api/v1/accounts.py and app/api/v1/steward.py. */

export type Kind = "group" | "entity" | "site"
export type RecordType = "account" | "contact"
export type Band = "auto_merge" | "review" | "distinct"

export interface AccountSummary {
  id: string
  kind: Kind
  site_type: string | null
  parent_id: string | null
  name: string
  gstin: string | null
  city: string | null
  state: string | null
  status: string
  merged_into_id: string | null
  hygiene_score: number | null
  tier: string | null
}

export interface HygieneDetails {
  score: number
  completeness: { points: number; of: number; missing: string[] }
  validity: { points: number; of: number; problems: string[]; warnings: string[] }
  freshness: { points: number; of: number; verified: number; tracked: number; unverified: string[] }
  duplicates: { points: number; of: number; open_candidates: number }
  computed_at: string
}

export interface Account extends AccountSummary {
  legal_name: string | null
  pan: string | null
  cin: string | null
  website: string | null
  domain: string | null
  email: string | null
  phone: string | null
  address_line1: string | null
  address_line2: string | null
  pincode: string | null
  country: string
  industry: string | null
  source: string
  owner_id?: string | null
  hygiene_details: HygieneDetails | null
  created_at: string
  updated_at: string
}

export interface Provenance {
  id: string
  field: string
  value: string | null
  source: string
  source_ref: string | null
  confidence: number
  status: string
  verified_at: string | null
  recorded_by: string | null
  created_at: string
}

export interface ContactSummary {
  id: string
  account_id: string | null
  full_name: string
  email: string | null
  phone: string | null
  designation: string | null
  status: string
  merged_into_id: string | null
  hygiene_score: number | null
}

export interface MergeSummary {
  id: string
  record_type: RecordType
  survivor_id: string
  merged_id: string
  automatic: boolean
  decided_by: string
  created_at: string
  undone_at: string | null
}

export interface MergeRow extends MergeSummary {
  survivor_name: string | null
  merged_name: string | null
}

export interface Choice {
  survivor_value?: string | null
  merged_value?: string | null
  take: "survivor" | "merged" | "created"
  reason: string
  site_id?: string
}

export interface MergeDetail extends MergeRow {
  field_choices: Record<string, Choice>
  repointed: Record<string, string[]>
  survivor_before: Record<string, unknown>
  merged_before: Record<string, unknown>
  undo_notes: string[] | null
  undone_by: string | null
}

export interface AccountDetail {
  account: Account
  provenance: Provenance[]
  proposals: Provenance[]
  children: AccountSummary[]
  contacts: ContactSummary[]
  merges: MergeSummary[]
  open_candidates: number
}

export interface Reason {
  signal: string
  detail: string
  effect: string
}

export interface Candidate {
  id: string
  record_type: RecordType
  left_id: string
  right_id: string
  score: number
  band: Band
  reasons: Reason[]
  status: string
  decided_by: string | null
  decided_at: string | null
  created_at: string
}

export interface CandidateRow extends Candidate {
  left: Record<string, string | number | null>
  right: Record<string, string | number | null>
}

export type RecordValues = Record<string, string | number | null>

export interface CandidateDetail {
  candidate: Candidate
  suggested_survivor_id: string
  records: Record<string, RecordValues>
  fields: string[]
  suggested_choices: Record<string, Choice>
}

export interface DuplicateCheck {
  problems: string[]
  warnings: string[]
  matches: { id: string; name: string; gstin: string | null; city: string | null; band: Band;
             score: number; reasons: Reason[] }[]
}

export interface ImportBatch {
  id: string
  record_type: RecordType
  filename: string
  status: "processing" | "done" | "failed"
  total_rows: number
  created_count: number
  auto_merged_count: number
  review_count: number
  error_count: number
  errors: { row?: number; problems: string[]; kept?: boolean }[]
  created_at: string
  finished_at: string | null
}

export interface HygieneBucket {
  active: number
  merged: number
  average_score: number | null
  distribution: Record<string, number>
  with_invalid_values: number
  open_duplicate_candidates: number
  lowest: { id: string; name: string | null; score: number | null }[]
}

export interface HygieneDashboard {
  accounts: HygieneBucket & { entities_without_gstin: number }
  contacts: HygieneBucket
}
