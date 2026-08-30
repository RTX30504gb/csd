import { usePolling } from './usePolling'
import { getDeployerAnalysis } from '../services/api'

interface DeployerAnalysisData {
  risk_score?: number
  risk_level?: string
  previous_launches?: number
  suspicious_launches?: number
  liquidity_withdrawals?: number
  recently_deployed_tokens?: Array<any>
  [key: string]: any
}

const useDeployerAnalysis = (address: string) => {
  const { data, loading, error } = usePolling(
    () => getDeployerAnalysis(address),
    { interval: 5000 } // Less frequent for deployer analysis as it changes slowly
  )

  return {
    data: data as DeployerAnalysisData,
    loading,
    error
  }
}

export default useDeployerAnalysis