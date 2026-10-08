export type Route = "qualified" | "nurture" | "disqualified";

export interface Citation {
  url: string;
  quote: string;
  date: string | null;
  page_date: string | null;
}

export interface Fact {
  id: string;
  field: string;
  value: string;
  number: number | null;
  citation: Citation;
}

export interface Signal {
  id: string;
  type: string;
  summary: string;
  detail: string | null;
  date: string | null;
  date_source: string;
  age_days: number | null;
  current: boolean;
  citation: Citation;
}

export interface Person {
  name: string;
  title: string;
  email: string | null;
  citation: Citation;
}

export interface InjectionFinding {
  url: string;
  text: string;
  rules: string[];
  score: number;
  hidden: boolean;
}

export interface Rejected {
  kind: string;
  value: string;
  url: string;
  quote: string;
  reason: string;
}

export interface Profile {
  domain: string;
  url: string;
  name: string;
  segment: string | null;
  industry: string | null;
  facts: Fact[];
  signals: Signal[];
  people: Person[];
  injection_findings: InjectionFinding[];
  pages: { url: string; title: string; page_date: string | null; fetched_at: string }[];
  skipped: [string, string][];
  rejected: Rejected[];
  blocked_by_robots: boolean;
  mode: string;
  model: string;
  llm_calls: number;
  seconds: number;
}

export interface RubricLine {
  criterion: string;
  points: number;
  max_points: number;
  reason: string;
  evidence: string[];
}

export interface Qualification {
  score: number;
  route: Route;
  lines: RubricLine[];
  disqualifiers: string[];
  rules_score: number;
  llm_adjustment: number;
  llm_reason: string;
  llm_evidence: string[];
  model: string;
}

export interface Contact {
  name: string;
  title: string;
  email: string | null;
  persona: string;
  source: string;
  citation: Citation | null;
  reason: string;
}

export interface AccountRow {
  id: number;
  domain: string;
  url: string;
  name: string;
  source: string;
  status: string;
  route: Route | null;
  score: number | null;
  timezone: string | null;
  do_not_contact: boolean;
  contact: Contact | null;
  error: string | null;
  signals: Signal[] | null;
  segment: string | null;
  injections: InjectionFinding[] | null;
  disqualifiers: string[] | null;
}

export interface Claim {
  text: string;
  evidence: string[];
  verdict: "supported" | "unsupported" | "unchecked";
  reason: string;
  checked_by: string[];
}

export interface Email {
  step: number;
  subject: string;
  body: string;
  claims: Claim[];
}

export interface CheckIssue {
  step: number;
  kind: string;
  message: string;
  severity: "block" | "warn";
  text: string;
}

export interface CheckReport {
  passed: boolean;
  issues: CheckIssue[];
  spam_score: number;
  readability: number;
  words: number[];
  personalization: number;
}

export interface DraftVariant {
  variant: string;
  angle: string;
  emails: Email[];
  report: CheckReport | null;
  attempts: number;
  history: { emails: Email[]; issues: string; trimmed?: boolean }[];
}

export interface DraftRow {
  id: number;
  account_id: number;
  variant: string;
  status: string;
  data: DraftVariant;
  first_draft: DraftVariant | null;
  contact: Contact;
  edited: Email[] | null;
  model: string;
  calls: number;
  reviewer: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface QueueItem {
  id: number;
  account_id: number;
  variant: string;
  status: string;
  contact: Contact;
  created_at: string;
  report: CheckReport | null;
  attempts: number;
  subject: string;
  name: string;
  domain: string;
  score: number;
  timezone: string;
}

export interface MessageRow {
  id: number;
  draft_id: number;
  account_id: number;
  step: number;
  to_email: string;
  to_name: string;
  subject: string;
  status: string;
  status_reason: string | null;
  scheduled_at: string;
  sent_at: string | null;
  timezone: string;
  approved_by: string | null;
  name: string;
}

export interface ReplyRow {
  id: number;
  from_email: string;
  subject: string;
  body: string | null;
  label: string | null;
  classification: {
    label: string;
    objection_type: string | null;
    confidence: number;
    reason: string;
    source: string;
    referral_email: string | null;
    resume_on: string | null;
    model: string;
  } | null;
  actions: string[] | null;
  received_at: string;
  name: string | null;
  domain: string | null;
  step: number | null;
  sent_subject: string | null;
}

export interface AuditRow {
  id: number;
  ts: string;
  actor: string;
  action: string;
  entity: string;
  entity_id: string | null;
  detail: Record<string, unknown>;
}
