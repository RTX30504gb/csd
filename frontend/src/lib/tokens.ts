/**
 * Shared token types + mock feed data.
 * Replace `mockTokens` / `makeRandomToken` with real API data — the shapes
 * match the backend response exactly.
 */

export interface TokenRisk {
  score: number;
  level: string;
  reasons: string[];
  computed_at: string;
}

export interface DetectedToken {
  contract_address: string;
  deployer: string;
  name: string;
  symbol: string;
  decimals: number;
  total_supply: string;
  creation_block: number;
  creation_timestamp: string;
  detected_at: string;
  risk?: TokenRisk | undefined;
}

export type RiskBucket = "critical" | "suspicious" | "low" | "unknown";

export function riskBucket(risk?: TokenRisk): RiskBucket {
  if (!risk) return "unknown";
  const level = risk.level?.toLowerCase() ?? "";
  if (level.includes("crit") || level.includes("high")) return "critical";
  if (level.includes("sus") || level.includes("med") || level.includes("warn"))
    return "suspicious";
  if (level.includes("low") || level.includes("safe") || level.includes("clean"))
    return "low";
  if (risk.score >= 70) return "critical";
  if (risk.score >= 40) return "suspicious";
  return "low";
}

export function shortAddress(address: string, head = 6, tail = 4): string {
  if (!address) return "0x??????";
  if (address.length <= head + tail + 2) return address;
  return `${address.slice(0, head)}\u2026${address.slice(-tail)}`;
}

export function timeAgo(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "--";
  const s = Math.max(0, Math.floor((now - then) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clockStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toISOString().slice(11, 19);
}
