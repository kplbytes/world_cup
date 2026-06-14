"""Tournament rules for 2026 World Cup."""

from __future__ import annotations

# 2026 FIFA World Cup tournament format
# 48 teams, 12 groups (A-L) of 4 teams each
# Group stage: 72 matches (6 per group)
# Round of 32: 16 matches (24 qualified + 8 best third-placed)
# Round of 16: 8 matches
# Quarter Finals: 4 matches
# Semi Finals: 2 matches
# Third Place: 1 match
# Final: 1 match
# Total: 104 matches

TEAMS_COUNT = 48
GROUPS_COUNT = 12
TEAMS_PER_GROUP = 4
GROUP_MATCHES = 72
KNOCKOUT_MATCHES = 32

STAGE_ORDER = [
    "group",
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "third_place",
    "final",
]

STAGE_DISPLAY_NAMES = {
    "group": "小组赛",
    "round_of_32": "32强",
    "round_of_16": "16强",
    "quarter_final": "四分之一决赛",
    "semi_final": "半决赛",
    "third_place": "三四名决赛",
    "final": "决赛",
}

# Qualification rules
QUALIFY_PER_GROUP = 2  # Top 2 qualify directly
BEST_THIRD_PLACED = 8  # 8 best third-placed teams qualify
TOTAL_KNOCKOUT_TEAMS = 32  # 24 + 8 = 32

# Knockout draw rules (simplified)
# The actual draw depends on which third-placed teams qualify
# and follows specific constraints to avoid same-group matchups early
