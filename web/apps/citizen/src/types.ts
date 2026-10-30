// 後端回應的形狀（對應 backend/app/api/me.py、family.py、consent/router.py）。

export interface Profile {
  name: string;
  age: number;
  age_band: string;
  village_code: string;
  village_name: string | null;
  elder_mode: boolean;
}

export interface Metric {
  metric: string;
  value: unknown;
  text: string;
}

export interface Decision {
  kind: "booking";
  site_id: string;
  site_name: string;
  slots: string[];
  text: string;
}

export interface ReportContent {
  date: string;
  title: string;
  status_sentence: string;
  risk: { level: "L0" | "L1" | "L2" | "L3"; triggers: string[] };
  metrics: Metric[];
  voice_script: string;
  lines: string[];
  decision: Decision | null;
  recommendation: { text: string; contraindications: string[] } | null;
  points: { balance: number; expiring: { expires_on: string; points: number }[] };
  events?: { event_id: string; title: string; when: string; venue: string; points_reward: number; reasons: string[] }[];
}

export interface Report {
  date: string;
  content: ReportContent;
}

export interface ReportBrief {
  date: string;
  status_sentence: string;
  risk_level: ReportContent["risk"]["level"] | null;
}

export interface Booking {
  id: string;
  site_name: string;
  slot_at: string;
  purpose: string;
}

export interface ActionResult {
  status: "confirmation_required" | "executed" | "failed";
  action_id: string;
  summary: string;
  result: Record<string, unknown> | null;
}

export interface PendingAction {
  id: string;
  status: string;
  summary: string;
  result: Record<string, unknown> | null;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface Offer {
  offer_id: string;
  title: string;
  points_cost: number;
}

export interface MerchantOffers {
  merchant_id: string;
  name: string;
  category: string;
  distance_km: number;
  offers: Offer[];
}

export interface PointsOut {
  balance: number;
  expiring: { expires_on: string; points: number }[];
  ledger: { delta: number; reason: string; balance_after: number; created_at: string }[];
  redemptions: {
    id: string;
    offer: string;
    points: number;
    qr_token: string | null;
    status: string;
    redeemed_at: string;
  }[];
}

export interface FamilyLink {
  link_id: string;
  user_id: string;
  display_name: string;
}

export interface FamilyOut {
  sharing: FamilyLink[];
  shared_with_me: FamilyLink[];
}

export interface FamilySummary {
  owner_display_name: string;
  status_sentence: string;
  risk_level: string | null;
  risk_label: string | null;
  pending_action: string | null;
}

export interface Consent {
  id: string;
  source: string;
  scope: { read: string[] };
}

export interface Explain {
  source: string;
  name: string;
  data: string;
  purpose: string;
  items: string[];
  who_sees: string[];
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string;
  payload: { report_date?: string; event_id?: string } | null;
  created_at: string;
  read_at: string | null;
}

// --- M8：健康儀表板、近日活動、推薦店家、推薦偏好（backend/app/tools/*、api/me_*.py） ---

export interface DailyValue {
  date: string;
  value: number;
}

export interface TimedValue {
  at: string;
  value: number;
}

export interface HealthSummary {
  period: "week" | "month" | "quarter";
  days: number;
  start: string;
  end: string;
  title: string;
  empty?: boolean;
  metrics: {
    steps?: {
      series: DailyValue[];
      avg: number | null;
      prev_avg: number | null;
      change_pct: number | null;
      goal: number;
      goal_days: number;
      days_with_data: number;
      peer_avg: number | null;
      peer_band: string;
    };
    heart_rate?: { series: DailyValue[]; avg: number | null };
    bp?: {
      series: { at: string; systolic: number; diastolic: number; site: string | null }[];
      count: number;
      reference: [number, number];
      high: number;
      in_range: number;
      avg?: [number, number];
      prev_avg?: [number, number];
    };
    weight?: { series: TimedValue[]; count: number; latest?: TimedValue; change?: number | null; bmi?: number | null };
    glucose?: { series: TimedValue[]; count: number; latest?: TimedValue; avg?: number };
  };
  not_authorized: string[];
  activity?: { points_earned: number; events_attended: number };
  lines: string[];
  note?: string;
}

export interface CommunityEvent {
  event_id: string;
  title: string;
  category: string;
  category_label: string;
  description: string;
  organizer: string;
  venue: string;
  start_at: string;
  end_at: string;
  when: string;
  capacity: number | null;
  points_reward: number;
  seats_left?: number | null;
  distance_km?: number | null;
  registration: "registered" | "attended" | "cancelled" | null;
  reasons?: string[];
  basis?: string[];
  points_granted?: number;
}

export interface EventsOut {
  events: CommunityEvent[];
  mine: CommunityEvent[];
  note?: string;
  radius_km?: number;
}

export interface RecommendedMerchant {
  merchant_id: string;
  name: string;
  category: string;
  rating: number;
  rating_count: number | null;
  tag_labels: string[];
  distance_km: number | null;
  reasons: string[];
  offers: { offer_id: string; title: string; points_cost: number; affordable: boolean }[];
}

export interface MerchantsOut {
  merchants: RecommendedMerchant[];
  min_rating: number;
  excluded_text: string;
}

export interface Option {
  value: string;
  label: string;
}

export interface RecommendPrefs {
  event_categories: string[];
  merchant_categories: string[];
  dietary: string[];
  min_rating: number | null;
  max_distance_km: number | null;
  event_push: boolean;
  rating_threshold: number;
  options: { event_categories: Option[]; merchant_categories: Option[]; dietary: Option[] };
}

export const SOURCES = ["wearable", "site", "nhi", "env", "merchant"] as const;
export const RISK_TONE = { L0: "ok", L1: "warn", L2: "alert", L3: "danger" } as const;
