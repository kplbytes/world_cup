import type { Dashboard, DecisionData } from "./types";

export async function getDashboard(): Promise<Dashboard> {
  const response = await fetch("/api/dashboard");
  if (!response.ok) throw new Error(`Dashboard request failed: ${response.status}`);
  return response.json();
}

export async function getDecision(): Promise<DecisionData> {
  const response = await fetch("/api/decision");
  if (!response.ok) throw new Error(`Decision request failed: ${response.status}`);
  return response.json();
}

export async function refreshDashboard(): Promise<void> {
  const response = await fetch("/api/refresh", { method: "POST" });
  if (!response.ok) throw new Error(`Refresh failed: ${response.status}`);
}

