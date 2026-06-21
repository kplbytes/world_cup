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
  if (isNaN(d.getTime())) return false;
  const now = reference;
  const future = new Date(now.getTime() + hours * 60 * 60 * 1000);
  // Include matches that started up to 3 hours ago (may be in progress)
  const past = new Date(now.getTime() - 3 * 60 * 60 * 1000);
  return d >= past && d < future;
}

export function isFinishedMatch(match: Pick<Match, "status" | "home_score" | "away_score">): boolean {
  const status = match.status.toLowerCase();
  if (["live", "in_play", "in_progress", "paused"].includes(status)) return false;
  return ["final", "finished", "completed"].includes(status)
    || (match.home_score != null && match.away_score != null);
}

export function isUpcomingMatch(
  match: Pick<Match, "kickoff" | "status" | "home_score" | "away_score">,
  reference: Date = new Date(),
): boolean {
  // A match is "upcoming" if it hasn't finished yet.
  // This includes: scheduled future matches, scheduled matches that have
  // passed their kickoff time (may be in progress but data not yet updated),
  // and matches explicitly marked as "live".
  return !isFinishedMatch(match);
}

export function isLiveMatch(
  match: Pick<Match, "kickoff" | "status" | "home_score" | "away_score">,
  reference: Date = new Date(),
): boolean {
  // A match is considered "live" if:
  // 1. Its status is explicitly "live", "in_progress", or "in_play"
  // 2. OR its kickoff time has passed but it's not finished yet
  //    (data source may not have updated the status yet)
  if (isFinishedMatch(match)) return false;
  const status = match.status.toLowerCase();
  if (["live", "in_play", "in_progress", "paused"].includes(status)) return true;
  // Check if kickoff time has passed (match likely in progress)
  const kickoff = parseUtcDate(match.kickoff);
  if (isNaN(kickoff.getTime())) return false;
  return kickoff <= reference;
}
