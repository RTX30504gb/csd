import { usePolling } from './usePolling'
import {
  getWallet,
  getWalletRelationships
} from '../services/api'

interface WalletData {
  wallet: any
  relationships: any
}

const useWalletData = (address: string) => {
  const { data: walletData, loading: walletLoading, error: walletError } = usePolling(
    () => getWallet(address),
    { interval: 3000 }
  )

  const { data: relationshipsData, loading: relationshipsLoading, error: relationshipsError } = usePolling(
    () => getWalletRelationships(address),
    { interval: 3000 }
  )

  const loading = walletLoading || relationshipsLoading
  const error = walletError || relationshipsError

  return {
    data: {
      wallet: walletData,
      relationships: relationshipsData
    } as WalletData,
    loading,
    error
  }
}

export default useWalletData