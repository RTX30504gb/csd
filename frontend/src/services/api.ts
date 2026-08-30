import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Token APIs
export const getToken = async (address: string) => {
  const response = await api.get(`/tokens/${address}`)
  return response.data
}

export const getTokenRisk = async (address: string) => {
  const response = await api.get(`/tokens/${address}/risk`)
  return response.data
}

export const getTokenHolders = async (address: string) => {
  const response = await api.get(`/tokens/${address}/holders`)
  return response.data
}

export const getTokenLiquidity = async (address: string) => {
  const response = await api.get(`/tokens/${address}/liquidity`)
  return response.data
}

export const getTokenPools = async (address: string) => {
  const response = await api.get(`/tokens/${address}/pools`)
  return response.data
}

// Pool APIs
export const getPoolEvents = async (poolAddress: string) => {
  const response = await api.get(`/pools/${poolAddress}/events`)
  return response.data
}

// Wallet APIs
export const getWallet = async (address: string) => {
  const response = await api.get(`/wallets/${address}`)
  return response.data
}

export const getWalletRelationships = async (
  address: string,
  limit: number = 100,
  kind?: string
) => {
  const response = await api.get(`/wallets/${address}/relationships`, {
    params: { limit, kind }
  })
  return response.data
}

export const getTokenWallets = async (address: string) => {
  const response = await api.get(`/tokens/${address}/wallets`)
  return response.data
}

// Other APIs
export const getAddressClassification = async (address: string) => {
  const response = await api.get(`/addresses/${address}/classification`)
  return response.data
}

export const getDeployerAnalysis = async (address: string) => {
  const response = await api.get(`/deployers/${address}/analysis`)
  return response.data
}

export const getRecentTokens = async (limit: number = 20) => {
  const response = await api.get(`/tokens/recent`, {
    params: { limit }
  })
  return response.data
}

export default api