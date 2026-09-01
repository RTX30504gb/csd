import usePolling from './usePolling'
import { getTokenPools, getPoolEvents } from '../services/api'

interface TokenLiquidityEventsData {
  events: Array<any>
}

const useTokenLiquidityEvents = (tokenAddress: string) => {
  // First get the token's pools, then get events for those pools
  const {
    data: poolsData,
    loading: poolsLoading,
    error: poolsError
  } = usePolling(
    () => getTokenPools(tokenAddress),
    { interval: 5000 } // Less frequent for pools as they don't change often
  )

  // For simplicity in MVP, we'll get events from the first pool only
  // In a full implementation, we'd merge events from all pools
  const firstPoolAddress = poolsData?.pools?.[0]?.pool_address

  const {
    data: eventsData,
    loading: eventsLoading,
    error: eventsError
  } = usePolling(
    () => firstPoolAddress ? getPoolEvents(firstPoolAddress) : { events: [] },
    { interval: 3000 } // More frequent for events
  )

  const loading = poolsLoading || eventsLoading
  const error = poolsError || eventsError

  // For MVP, just return events from first pool
  // TODO: Merge events from all pools and sort by time
  const data = {
    events: eventsData?.events || []
  }

  return {
    data: data as TokenLiquidityEventsData,
    loading,
    error
  }
}

export default useTokenLiquidityEvents