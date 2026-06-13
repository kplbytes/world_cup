# Findings

## Repository
- The project directory was empty on 2026-06-13 and was not a Git repository.

## Product Reference
- The referenced Xiaohongshu system shows groups A-L, team ratings, match probabilities, xG, likely scores, qualification probabilities, detailed explanations, squads, player values, recent form, and data confidence/provenance.

## Data Sources
- FIFA official scores/fixtures page is the authoritative verification source for fixtures and final results.
- football-data.org documents a free World Cup competition feed for fixtures/results/standings; it requires a free API token.
- openfootball/worldcup.json provides public-domain 2026 World Cup data with no API key and is suitable for baseline fixtures/teams and offline snapshots.
- FIFA publishes the official men's ranking and ranking points; the current page reports an update dated 2026-06-11.
- martj42/international_results is a public-domain historical international-results dataset suitable for reproducible Elo/form model training.
- Community 2026 APIs exist without keys, but they are not authoritative enough to be the sole live source.
- A replaceable adapter layer plus persisted snapshots is required because free providers can change limits or schemas.
- There is no equally stable, official, fully free API for live player market values. Squad/value data must remain an optional enrichment source and cannot be required for prediction correctness.
- China Sports Lottery's mobile football calculator loads public match data from `webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry`. Its page code exposes match IDs, dates, status, teams, leagues, ranks, pool status, and HAD/H-HAD odds.
- The Sporttery endpoint is protected by Tencent Cloud WAF for non-browser requests, and its inventory is a betting sales pool rather than a guaranteed complete tournament feed. It is suitable only as an optional schedule/market cross-check, not as the sole fixture or final-result authority.
- If Sporttery decimal HAD odds are used, they must be converted to normalized implied probabilities by dividing each reciprocal price by the sum of all three reciprocal prices. The raw overround must be stored and displayed; market probabilities must not silently replace model probabilities.

## Prediction Design Direction
- Use a transparent ensemble: time-decayed Elo strength plus a Poisson score model, calibrated from historical international results.
- Produce win/draw/loss probabilities and scoreline probabilities from the score matrix.
- Simulate all remaining group matches many times to calculate first/second/third/qualification probabilities.
- After a final result arrives, persist it, recompute standings, update team form/Elo, and rerun remaining-match and qualification simulations.

## Tournament Rules
- 48 teams in 12 groups of four.
- Top two teams in each group plus the eight best third-place teams advance to the round of 32.
