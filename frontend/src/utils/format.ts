/** Format a number to fixed decimal digits (default 4). */
export function fmt(n: number, digits = 4): string {
  return n.toFixed(digits);
}

/** Format a 0-1 ratio as a percentage string like "45.0%". */
export function pct(n: number): string {
  return (n * 100).toFixed(1) + "%";
}
