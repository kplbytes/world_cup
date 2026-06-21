# Team Profile Trait Validation

## Sources
- Local profile database: 48 teams, generated from the current workspace database.
- World Football Elo Ratings: `https://eloratings.net/World.tsv`, matched 46/48 teams.
- FIFA official ranking page: `https://inside.fifa.com/fifa-world-ranking/men`, last official update `11 June 2026`, profile rank coverage 48/48 teams.
- StatsBomb World Cup xG file: `data/external/statsbomb/world_cup_xg.json`, 128 matches, seasons 2018, 2022, competitions World Cup, source matched 30/48 teams, profile exposes xG for 30/48 teams.
- 2026 venue registry: `data/seed/world-cup-2026-venues.json`, travel distance 48/48, timezone shift 48/48, environment score 48/48 teams.
- Open-Meteo historical climate baseline: `data/seed/world-cup-2026-venue-climate.json`, climate baseline 48/48 teams.
- FIFA official squad list: `data/seed/world-cup-2026-squads.json`, squad coverage 48/48 teams.
- Traceable profile source_list coverage: 48/48 teams.

## Summary
- Average trait count: 7.02
- Data quality score range: 66.2 - 71.5
- Teams with validation issues: 0

## Issues
- No validation issues detected by the current checks.

## Sample Rows

- `ALG` Algeria: Elo=1764 / WorldEloRank=33, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 预选赛抢分强, 预选赛稳定
- `ARG` Argentina: Elo=2123 / WorldEloRank=2, traits=稳定破门, 进攻输出中等, 防线稳固, 零封能力强, 节奏均衡, 近期结果稳定, 防守优先, 大赛经验丰富, 淘汰赛履历强, 杯赛压力表现好, 预选赛抢分强, 预选赛稳定
- `AUS` Australia: Elo=1783 / WorldEloRank=26, traits=稳定破门, 进攻输出中等, 防线稳固, 零封能力强, 节奏均衡, 近期结果稳定, 预选赛稳定
- `AUT` Austria: Elo=1846 / WorldEloRank=21, traits=稳定破门, 进攻输出中等, 防守中等稳健, 双方进球概率高, 节奏均衡, 近期结果稳定, 预选赛抢分强, 预选赛稳定
- `BEL` Belgium: Elo=1884 / WorldEloRank=16, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 近期结果稳定, 预选赛抢分强, 预选赛稳定
- `BIH` Bosnia & Herzegovina: Elo=1594 / WorldEloRank=65, traits=进攻输出偏弱, 防守波动偏大, 失球压力高, 双方进球概率高, 节奏均衡, 近期结果稳定
- `BRA` Brazil: Elo=1987 / WorldEloRank=6, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 淘汰赛履历强, 杯赛压力表现好
- `CAN` Canada: Elo=1787 / WorldEloRank=28, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 近期结果稳定, 预选赛抢分强
- `CIV` Cote d'Ivoire: Elo=1718 / WorldEloRank=NA, traits=进攻输出中等, 防线稳固, 零封能力强, 节奏均衡, 近期结果稳定, 遇强韧性高, 防守优先, 预选赛抢分强, 预选赛稳定
- `COD` Congo DR: Elo=1667 / WorldEloRank=NA, traits=进攻输出偏弱, 防线稳固, 低比分倾向, 比赛节奏保守, 近期结果稳定
- `COL` Colombia: Elo=1991 / WorldEloRank=5, traits=进攻输出中等, 防守中等稳健, 节奏均衡
- `CPV` Cabo Verde: Elo=1597 / WorldEloRank=62, traits=进攻输出偏弱, 进攻哑火风险, 防守中等稳健, 低比分倾向, 比赛节奏保守, 近期结果稳定, 遇强韧性高, 防守优先
- `CRO` Croatia: Elo=1893 / WorldEloRank=15, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 近期结果稳定, 大赛经验丰富, 淘汰赛履历强, 杯赛压力表现好, 预选赛抢分强, 预选赛稳定
- `CUW` Curaçao: Elo=1447 / WorldEloRank=94, traits=进攻哑火风险, 防守波动偏大, 开放对攻倾向, 遇强抗压不足, 预选赛抢分强, 预选赛稳定
- `CZE` Czechia: Elo=1702 / WorldEloRank=48, traits=进攻输出中等, 防守波动偏大, 开放对攻倾向, 双方进球概率高, 节奏均衡, 遇强抗压不足, 预选赛稳定
- `ECU` Ecuador: Elo=1888 / WorldEloRank=13, traits=进攻输出偏弱, 进攻哑火风险, 防线稳固, 低比分倾向, 比赛节奏保守, 近期结果稳定, 平局倾向高, 遇强韧性高, 防守优先, 预选赛稳定
- `EGY` Egypt: Elo=1706 / WorldEloRank=42, traits=进攻输出中等, 防线稳固, 低比分倾向, 近期结果稳定, 平局倾向高, 预选赛抢分强, 预选赛稳定
- `ENG` England: Elo=2043 / WorldEloRank=4, traits=进攻火力强, 稳定破门, 防线稳固, 节奏均衡, 近期结果稳定, 淘汰赛履历强, 小组赛稳定, 预选赛抢分强, 预选赛稳定
- `ESP` Spain: Elo=2138 / WorldEloRank=1, traits=进攻火力顶级, 稳定破门, 防线稳固, 开放对攻倾向, 近期结果稳定, 防守优先, 预选赛抢分强, 预选赛稳定
- `FRA` France: Elo=2076 / WorldEloRank=3, traits=进攻火力强, 稳定破门, 防守中等稳健, 节奏均衡, 近期结果稳定, 防守优先, 大赛经验丰富, 淘汰赛履历强, 杯赛压力表现好, 预选赛抢分强, 预选赛稳定
- `GER` Germany: Elo=1945 / WorldEloRank=9, traits=进攻火力顶级, 稳定破门, 防守中等稳健, 开放对攻倾向, 双方进球概率高, 预选赛抢分强, 预选赛稳定
- `GHA` Ghana: Elo=1541 / WorldEloRank=73, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 遇强抗压不足, 预选赛稳定
- `HAI` Haiti: Elo=1535 / WorldEloRank=78, traits=进攻产量高, 防守波动偏大, 开放对攻倾向, 双方进球概率高, 预选赛抢分强, 预选赛稳定
- `IRN` IR Iran: Elo=1761 / WorldEloRank=34, traits=进攻输出中等, 防守中等稳健, 开放对攻倾向, 近期结果稳定, 强队翻车风险, 预选赛抢分强, 预选赛稳定
- `IRQ` Iraq: Elo=1598 / WorldEloRank=66, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 遇强抗压不足, 预选赛稳定
- `JOR` Jordan: Elo=1664 / WorldEloRank=55, traits=稳定破门, 进攻输出中等, 防线稳固, 节奏均衡, 预选赛稳定
- `JPN` Japan: Elo=1908 / WorldEloRank=11, traits=进攻火力顶级, 稳定破门, 防线稳固, 零封能力强, 开放对攻倾向, 近期结果稳定, 防守优先, 预选赛抢分强, 预选赛稳定
- `KOR` Korea Republic: Elo=1771 / WorldEloRank=30, traits=稳定破门, 进攻输出中等, 防守中等稳健, 节奏均衡, 近期结果稳定, 预选赛抢分强, 预选赛稳定
- `KSA` Saudi Arabia: Elo=1590 / WorldEloRank=64, traits=进攻输出偏弱, 进攻哑火风险, 防守中等稳健, 节奏均衡, 预选赛稳定
- `MAR` Morocco: Elo=1853 / WorldEloRank=20, traits=进攻火力强, 稳定破门, 防线稳固, 零封能力强, 节奏均衡, 近期结果稳定, 防守优先, 大赛经验丰富, 淘汰赛履历强, 小组赛稳定, 预选赛抢分强, 预选赛稳定
- `MEX` Mexico: Elo=1896 / WorldEloRank=12, traits=进攻哑火风险, 防线稳固, 零封能力强, 低比分倾向, 近期结果稳定, 预选赛抢分强, 预选赛稳定
- `NED` Netherlands: Elo=1961 / WorldEloRank=7, traits=进攻火力强, 稳定破门, 防守中等稳健, 开放对攻倾向, 双方进球概率高, 近期结果稳定, 淘汰赛履历强, 杯赛压力表现好, 小组赛稳定, 预选赛抢分强, 预选赛稳定
- `NOR` Norway: Elo=1923 / WorldEloRank=10, traits=进攻火力顶级, 稳定破门, 防守中等稳健, 开放对攻倾向, 双方进球概率高, 近期结果稳定, 胜负分明, 预选赛抢分强, 预选赛稳定
- `NZL` New Zealand: Elo=1573 / WorldEloRank=70, traits=进攻产量高, 防线稳固, 零封能力强, 开放对攻倾向, 近期波动明显, 胜负分明, 预选赛抢分强, 预选赛稳定
- `PAN` Panama: Elo=1699 / WorldEloRank=49, traits=进攻输出中等, 防守中等稳健, 节奏均衡, 近期结果稳定, 预选赛稳定
- `PAR` Paraguay: Elo=1818 / WorldEloRank=24, traits=进攻输出偏弱, 进攻哑火风险, 防守中等稳健, 节奏均衡
- `POR` Portugal: Elo=1974 / WorldEloRank=8, traits=进攻火力顶级, 开放对攻倾向, 近期结果稳定, 防守优先, 淘汰赛履历强, 预选赛抢分强, 预选赛稳定
- `QAT` Qatar: Elo=1429 / WorldEloRank=91, traits=进攻输出中等, 防守波动偏大, 失球压力高, 开放对攻倾向, 双方进球概率高, 节奏均衡
- `RSA` South Africa: Elo=1521 / WorldEloRank=79, traits=进攻输出中等, 防守中等稳健, 双方进球概率高, 节奏均衡, 平局倾向高, 预选赛稳定
- `SCO` Scotland: Elo=1773 / WorldEloRank=31, traits=进攻输出中等, 防守波动偏大, 开放对攻倾向, 节奏均衡, 预选赛抢分强, 预选赛稳定
- `SEN` Senegal: Elo=1847 / WorldEloRank=22, traits=稳定破门, 进攻输出中等, 防线稳固, 节奏均衡, 预选赛抢分强, 预选赛稳定
- `SUI` Switzerland: Elo=1884 / WorldEloRank=14, traits=进攻输出中等, 防守波动偏大, 双方进球概率高, 节奏均衡, 近期结果稳定, 平局倾向高, 预选赛稳定
- `SWE` Sweden: Elo=1722 / WorldEloRank=39, traits=进攻输出中等, 防守波动偏大, 失球压力高, 开放对攻倾向, 近期波动明显, 遇强抗压不足
- `TUN` Tunisia: Elo=1602 / WorldEloRank=69, traits=进攻哑火风险, 防线稳固, 零封能力强, 低比分倾向, 防守优先, 预选赛抢分强, 预选赛稳定
- `TUR` Türkiye: Elo=1850 / WorldEloRank=25, traits=稳定破门, 进攻输出中等, 防守中等稳健, 开放对攻倾向, 双方进球概率高, 预选赛抢分强, 预选赛稳定
- `URU` Uruguay: Elo=1878 / WorldEloRank=17, traits=进攻哑火风险, 防线稳固, 零封能力强, 低比分倾向, 平局倾向高, 防守优先, 预选赛稳定
- `USA` United States: Elo=1797 / WorldEloRank=23, traits=进攻火力强, 稳定破门, 防守中等稳健, 节奏均衡, 小组赛稳定
- `UZB` Uzbekistan: Elo=1705 / WorldEloRank=47, traits=进攻输出中等, 防线稳固, 零封能力强, 节奏均衡, 近期结果稳定, 遇强韧性高, 强队翻车风险, 预选赛抢分强, 预选赛稳定
