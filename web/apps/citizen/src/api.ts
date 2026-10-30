import { createApi } from "@healthapp/ui";

export const api = createApi({ storageKey: "healthapp.citizen.token" });

export function when(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function day(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
