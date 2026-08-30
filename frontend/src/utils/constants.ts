export const API_ENDPOINTS = {
  TOKENS: '/tokens',
  TOKEN_DETAIL: (address: string) => `/tokens/${address}`,
  TOKEN_RISK: (address: string) => `/tokens/${address}/risk`,
  TOKEN_HOLDERS: (address: string) => `/tokens/${address}/holders`,
  TOKEN_LIQUIDITY: (address: string) => `/tokens/${address}/liquidity`,
  TOKEN_POOLS: (address: string) => `/tokens/${address}/pools`,
  WALLET_DETAIL: (address: string) => `/wallets/${address}`,
  WALLET_RELATIONSHIPS: (address: string) => `/wallets/${address}/relationships`,
  TOKEN_WALLETS: (address: string) => `/tokens/${address}/wallets`,
  ADDRESS_CLASSIFICATION: (address: string) => `/addresses/${address}/classification`,
  DEPLOYER_ANALYSIS: (address: string) => `/deployers/${address}/analysis`,
  RECENT_TOKENS: '/tokens/recent'
}

export const RISK_LEVELS = {
  LOW: { min: 0, max: 29, label: 'Low', color: '#4caf50' },
  SUSPICIOUS: { min: 30, max: 54, label: 'Suspicious', color: '#ff9800' },
  HIGH: { min: 55, max: 79, label: 'High', color: '#f44336' },
  CRITICAL: { min: 80, max: 100, label: 'Critical', color: '#9c27b0' }
}

export const POLLING_INTERVALS = {
  FAST: 2000,    // 2 seconds
  STANDARD: 3000, // 3 seconds
  SLOW: 5000     // 5 seconds
}