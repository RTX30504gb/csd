import { DetectedToken } from "./tokens";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function fetchRecentTokens(limit = 20): Promise<DetectedToken[]> {
  try {
    const response = await fetch(`${API_BASE_URL}/tokens/recent?limit=${limit}`);
    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }
    const data = await response.json();
    return data.tokens;
  } catch (error) {
    console.error("Failed to fetch recent tokens:", error);
    throw error;
  }
}

export async function fetchTokenDetail(address: string) {
  try {
    const response = await fetch(`${API_BASE_URL}/tokens/${address}`);
    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }
    return await response.json();
  } catch (error) {
    console.error(`Failed to fetch token detail for ${address}:`, error);
    throw error;
  }
}
