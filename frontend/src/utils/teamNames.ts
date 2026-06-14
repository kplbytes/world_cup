/**
 * Team name mapping - Chinese name + English code
 * Covers all 48 teams for 2026 FIFA World Cup
 */

const TEAM_ZH_NAMES: Record<string, string> = {
  // A组
  MEX: "墨西哥", RSA: "南非", KOR: "韩国", CZE: "捷克",
  // B组
  CAN: "加拿大", BIH: "波黑", QAT: "卡塔尔", SUI: "瑞士",
  // C组
  BRA: "巴西", MAR: "摩洛哥", HAI: "海地", HTI: "海地", SCO: "苏格兰",
  // D组
  USA: "美国", PAR: "巴拉圭", AUS: "澳大利亚", TUR: "土耳其",
  // E组
  GER: "德国", CUW: "库拉索", CIV: "科特迪瓦", ECU: "厄瓜多尔",
  // F组
  NED: "荷兰", JPN: "日本", SWE: "瑞典", TUN: "突尼斯",
  // G组
  BEL: "比利时", EGY: "埃及", IRN: "伊朗", IRI: "伊朗", NZL: "新西兰",
  // H组
  ESP: "西班牙", CPV: "佛得角", KSA: "沙特阿拉伯", URU: "乌拉圭",
  // I组
  FRA: "法国", SEN: "塞内加尔", IRQ: "伊拉克", NOR: "挪威",
  // J组
  ARG: "阿根廷", DZA: "阿尔及利亚", ALG: "阿尔及利亚", AUT: "奥地利", JOR: "约旦",
  // K组
  POR: "葡萄牙", COD: "刚果民主共和国", UZB: "乌兹别克斯坦", COL: "哥伦比亚",
  // L组
  ENG: "英格兰", CRO: "克罗地亚", GHA: "加纳", PAN: "巴拿马",
};

const ENGLISH_TO_ZH: Record<string, string> = {
  "Mexico": "墨西哥",
  "South Africa": "南非",
  "Korea Republic": "韩国",
  "South Korea": "韩国",
  "Czechia": "捷克",
  "Czech Republic": "捷克",
  "Canada": "加拿大",
  "Bosnia & Herzegovina": "波黑",
  "Bosnia and Herzegovina": "波黑",
  "Qatar": "卡塔尔",
  "Switzerland": "瑞士",
  "Brazil": "巴西",
  "Morocco": "摩洛哥",
  "Haiti": "海地",
  "Scotland": "苏格兰",
  "United States": "美国",
  "USA": "美国",
  "Paraguay": "巴拉圭",
  "Australia": "澳大利亚",
  "Türkiye": "土耳其",
  "Turkey": "土耳其",
  "Germany": "德国",
  "Curaçao": "库拉索",
  "Curacao": "库拉索",
  "Cote d'Ivoire": "科特迪瓦",
  "Côte d'Ivoire": "科特迪瓦",
  "Ivory Coast": "科特迪瓦",
  "Ecuador": "厄瓜多尔",
  "Netherlands": "荷兰",
  "Holland": "荷兰",
  "Japan": "日本",
  "Sweden": "瑞典",
  "Tunisia": "突尼斯",
  "Belgium": "比利时",
  "Egypt": "埃及",
  "IR Iran": "伊朗",
  "Iran": "伊朗",
  "New Zealand": "新西兰",
  "Spain": "西班牙",
  "Cabo Verde": "佛得角",
  "Cape Verde": "佛得角",
  "Cape Verde Islands": "佛得角",
  "Saudi Arabia": "沙特阿拉伯",
  "Uruguay": "乌拉圭",
  "France": "法国",
  "Senegal": "塞内加尔",
  "Iraq": "伊拉克",
  "Norway": "挪威",
  "Argentina": "阿根廷",
  "Algeria": "阿尔及利亚",
  "Austria": "奥地利",
  "Jordan": "约旦",
  "Portugal": "葡萄牙",
  "Congo DR": "刚果民主共和国",
  "DR Congo": "刚果民主共和国",
  "Democratic Republic of Congo": "刚果民主共和国",
  "Uzbekistan": "乌兹别克斯坦",
  "Colombia": "哥伦比亚",
  "England": "英格兰",
  "Croatia": "克罗地亚",
  "Ghana": "加纳",
  "Panama": "巴拿马",
};

export function getTeamDisplayName(code: string): string {
  const zh = TEAM_ZH_NAMES[code];
  if (zh) return `${zh} ${code}`;
  return code;
}

export function getTeamZhName(code: string): string {
  // First try code mapping
  const zh = TEAM_ZH_NAMES[code];
  if (zh) return zh;
  // Then try English alias mapping
  const enZh = ENGLISH_TO_ZH[code];
  if (enZh) return enZh;
  return code;
}

export function getTeamCode(code: string): string {
  return code;
}

/**
 * Best-effort Chinese team name from any input.
 * Priority: code mapping > name mapping > already-Chinese > fallback
 */
export function getTeamDisplayNameFromAny(input: string | null | undefined, code?: string | null): string {
  if (!input && !code) return '未知';
  // Priority 1: code hits 3-letter mapping
  if (code && TEAM_ZH_NAMES[code]) return TEAM_ZH_NAMES[code];
  // Priority 2: input hits 3-letter mapping (e.g. input is "HAI")
  if (input && TEAM_ZH_NAMES[input]) return TEAM_ZH_NAMES[input];
  // Priority 3: input hits English alias mapping
  if (input && ENGLISH_TO_ZH[input]) return ENGLISH_TO_ZH[input];
  // Priority 4: input is already Chinese (contains CJK characters)
  if (input && /[\u4e00-\u9fff]/.test(input)) return input;
  // Fallback
  return input || code || '未知';
}

/**
 * Get display name for a team from TeamRef {id, name, short_name, flag}.
 * Uses the name field first (which may already be Chinese from backend),
 * falls back to code mapping, then English alias mapping.
 */
export function getTeamDisplayFromRef(team: { id: string; name?: string; short_name?: string } | null | undefined): string {
  if (!team) return '未知';
  // If name is already Chinese, use it
  if (team.name && /[\u4e00-\u9fff]/.test(team.name)) return team.name;
  if (team.short_name && /[\u4e00-\u9fff]/.test(team.short_name)) return team.short_name;
  // Try code mapping
  if (TEAM_ZH_NAMES[team.id]) return TEAM_ZH_NAMES[team.id];
  // Try English name mapping
  if (team.name && ENGLISH_TO_ZH[team.name]) return ENGLISH_TO_ZH[team.name];
  if (team.short_name && ENGLISH_TO_ZH[team.short_name]) return ENGLISH_TO_ZH[team.short_name];
  // Last resort
  return team.name || team.short_name || team.id;
}
