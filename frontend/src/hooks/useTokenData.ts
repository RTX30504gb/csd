import usePolling from './usePolling'
import {
  getToken,
  getTokenRisk,
  getTokenHolders,
  getTokenLiquidity,
  getTokenPools
} from '../services/api'

interface TokenData {
  token: any
  risk: any
  holders: any
  liquidity: any
  pools: any
}

const useTokenData = (address: string) => {
  const { data: tokenData, loading: tokenLoading, error: tokenError } = usePolling(
    () => getToken(address),
    { interval: 3000 }
  )

  const { data: riskData, loading: riskLoading, error: riskError } = usePolling(
    () => getTokenRisk(address),
    { interval: 3000 }
  )

  const { data: holdersData, loading: holdersLoading, error: holdersError } = usePolling(
    () => getTokenHolders(address),
    { interval: 3000 }
  )

  const { data: liquidityData, loading: liquidityLoading, error: liquidityError } = usePolling(
    () => getTokenLiquidity(address),
    { interval: 3000 }
  )

  const { data: poolsData, loading: poolsLoading, error: poolsError } = usePolling(
    () => getTokenPools(address),
    { interval: 3000 }
  )

  const loading =
    tokenLoading ||
    riskLoading ||
    holdersLoading ||
    liquidityLoading ||
    poolsLoading

  const error =
    tokenError ||
    riskError ||
    holdersError ||
    liquidityError ||
    poolsError

  return {
    data: {
      token: tokenData,
      risk: riskData,
      holders: holdersData,
      liquidity: liquidityData,
      pools: poolsData
    } as TokenData,
    loading,
    error
  }
}

export default useTokenData