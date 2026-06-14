import { describe, it, expect } from "vitest";
import {
  formatChinaTimeShort,
  formatChinaDateTime,
  isFinishedMatch,
  isUpcomingMatch,
  isWithinNextHoursChina,
} from "../utils/time";
import type { Match } from "../types";
import { getTeamDisplayName, getTeamZhName } from "../utils/teamNames";

describe("Time utilities", () => {
  it("converts UTC to Beijing time", () => {
    // 2026-06-18T04:00:00Z = 2026-06-18 12:00 Beijing
    const result = formatChinaTimeShort("2026-06-18T04:00:00Z");
    expect(result).toContain("12:00");
  });

  it.each([
    ["2026-06-14T04:00:00Z", "06/14 12:00"],
    ["2026-06-14T17:00:00", "06/15 01:00"],
    ["2026-06-14T17:00:00Z", "06/15 01:00"],
    ["2026-06-14T20:00:00Z", "06/15 04:00"],
    ["2026-06-14T23:00:00Z", "06/15 07:00"],
  ])("formats %s as Beijing time %s", (value, expected) => {
    expect(formatChinaTimeShort(value)).toBe(expected);
  });

  it("uses an inclusive start and exclusive end for future hour windows", () => {
    const now = new Date("2026-06-14T04:00:00Z");
    expect(isWithinNextHoursChina("2026-06-14T04:00:00Z", 24, now)).toBe(true);
    expect(isWithinNextHoursChina("2026-06-15T03:59:59Z", 24, now)).toBe(true);
    expect(isWithinNextHoursChina("2026-06-15T04:00:00Z", 24, now)).toBe(false);
    expect(isWithinNextHoursChina("2026-06-14T03:59:59Z", 24, now)).toBe(false);
  });

  it("treats final-like statuses or a complete score as finished", () => {
    const match = (overrides: Partial<Match>) => ({
      status: "scheduled",
      home_score: null,
      away_score: null,
      ...overrides,
    }) as Match;

    expect(isFinishedMatch(match({ status: "final" }))).toBe(true);
    expect(isFinishedMatch(match({ status: "completed" }))).toBe(true);
    expect(isFinishedMatch(match({ status: "finished" }))).toBe(true);
    expect(isFinishedMatch(match({ home_score: 2, away_score: 0 }))).toBe(true);
    expect(isFinishedMatch(match({ home_score: 2, away_score: null }))).toBe(false);
  });

  it("only treats future, unfinished matches as upcoming", () => {
    const now = new Date("2026-06-14T04:00:00Z");
    const match = (overrides: Partial<Match>) => ({
      kickoff: "2026-06-14T05:00:00Z",
      status: "scheduled",
      home_score: null,
      away_score: null,
      ...overrides,
    }) as Match;

    expect(isUpcomingMatch(match({}), now)).toBe(true);
    expect(isUpcomingMatch(match({ status: "final" }), now)).toBe(false);
    expect(isUpcomingMatch(match({ home_score: 1, away_score: 0 }), now)).toBe(false);
    expect(isUpcomingMatch(match({ kickoff: "2026-06-14T03:59:59Z" }), now)).toBe(false);
  });

  it("shows 时间待确认 for null", () => {
    expect(formatChinaDateTime(null)).toBe("时间待确认");
  });

  it("shows 时间待确认 for invalid", () => {
    expect(formatChinaDateTime("invalid")).toBe("时间待确认");
  });
});

describe("Team names", () => {
  it("ESP shows as 西班牙 ESP", () => {
    expect(getTeamDisplayName("ESP")).toBe("西班牙 ESP");
  });

  it("ARG shows as 阿根廷 ARG", () => {
    expect(getTeamDisplayName("ARG")).toBe("阿根廷 ARG");
  });

  it("FRA shows as 法国 FRA", () => {
    expect(getTeamDisplayName("FRA")).toBe("法国 FRA");
  });

  it("unknown code falls back to code", () => {
    expect(getTeamDisplayName("XXX")).toBe("XXX");
  });

  it("getTeamZhName returns Chinese name", () => {
    expect(getTeamZhName("ESP")).toBe("西班牙");
  });
});
