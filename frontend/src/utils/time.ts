/**
 * Time formatting utilities - all times displayed in China Standard Time (UTC+8)
 */

import type { Match } from "../types";

const CHINA_TZ = "Asia/Shanghai";

function parseUtcDate(value: string | Date): Date {
  if (value instanceof Date) return value;
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

export function formatChinaDateTime(value: string | Date | null | undefined): string {
  if (!value) return "时间待确认";
  try {
    const d = parseUtcDate(value);
    if (isNaN(d.getTime())) return "时间待确认";
    return d.toLocaleString("zh-CN", {
      timeZone: CHINA_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }) + " 北京时间";
  } catch {
    return "时间待确认";
  }
}

export function formatChinaTime(value: string | Date | null | undefined): string {
  if (!value) return "时间待确认";
  try {
    const d = parseUtcDate(value);
    if (isNaN(d.getTime())) return "时间待确认";
    const str = d.toLocaleString("zh-CN", {
      timeZone: CHINA_TZ,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    return str + " 北京时间";
  } catch {
    return "时间待确认";
  }
}

export function formatChinaDate(value: string | Date | null | undefined): string {
  if (!value) return "时间待确认";
  try {
    const d = parseUtcDate(value);
    if (isNaN(d.getTime())) return "时间待确认";
    return d.toLocaleDateString("zh-CN", {
      timeZone: CHINA_TZ,
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return "时间待确认";
  }
}

export function formatChinaTimeShort(value: string | Date | null | undefined): string {
  if (!value) return "待确认";
  try {
    const d = parseUtcDate(value);
    if (isNaN(d.getTime())) return "待确认";
    return d.toLocaleString("zh-CN", {
      timeZone: CHINA_TZ,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return "待确认";
  }
}

export function getChinaDateKey(value: string | Date): string {
  const d = parseUtcDate(value);
  return d.toLocaleDateString("sv-SE", { timeZone: CHINA_TZ });
}

export function isSameChinaDate(value: string | Date, reference: Date = new Date()): boolean {
  return getChinaDateKey(value) === getChinaDateKey(reference);
}

export function isWithinNextHoursChina(value: string | Date, hours: number, reference: Date = new Date()): boolean {
  const d = parseUtcDate(value);
  const now = reference;
  const future = new Date(now.getTime() + hours * 60 * 60 * 1000);
  return !isNaN(d.getTime()) && d >= now && d < future;
}

export function isFinishedMatch(match: Pick<Match, "status" | "home_score" | "away_score">): boolean {
  const status = match.status.toLowerCase();
  return ["final", "finished", "completed"].includes(status)
    || (match.home_score != null && match.away_score != null);
}

export function isUpcomingMatch(
  match: Pick<Match, "kickoff" | "status" | "home_score" | "away_score">,
  reference: Date = new Date(),
): boolean {
  const kickoff = parseUtcDate(match.kickoff);
  return !isNaN(kickoff.getTime()) && kickoff >= reference && !isFinishedMatch(match);
}
