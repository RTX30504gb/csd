import { useState, useCallback } from 'react'
import api from '../services/api'

interface ApiState<T> {
  data: T | null
  loading: boolean
  error: Error | null
}

const useApi = <T,>() => {
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    loading: false,
    error: null
  })

  const execute = useCallback(async (apiCall: Promise<any>) => {
    setState(prev => ({ ...prev, loading: true, error: null }))
    try {
      const result = await apiCall
      setState({ data: result.data, loading: false, error: null })
      return result.data
    } catch (err) {
      setState(prev => ({ ...prev, loading: false, error: err as Error }))
      throw err
    }
  }, [])

  const reset = useCallback(() => {
    setState({ data: null, loading: false, error: null })
  }, [])

  return { ...state, execute, reset }
}

export default useApi