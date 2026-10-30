import { createApi } from "@healthapp/ui";

export const api = createApi({ storageKey: "healthapp.site.token" });

export interface Board {
  site_id: string;
  site_name: string;
  date: string;
  bookings: { slot_at: string; count: number }[];
  booking_total: number;
  care_tasks: { total: number; open: number; visited: number };
  equipment: string;
}

export interface CareTask {
  code: string;
  task_type: string;
  rule_id: string;
  status: string;
  created_at: string;
  decoded: boolean;
}

export interface Decoded {
  code: string;
  task_type: string;
  name: string;
  display_name: string;
  phone: string | null;
  address: string | null;
  visit_guide: string[];
}

export function hhmm(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
