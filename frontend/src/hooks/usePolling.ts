import { useEffect, useRef } from 'react'

interface UsePollingOptions {
  interval: number
  immediate?: boolean
}

const usePolling = <T>(
  fetchFn: () => Promise<T>,
  options: UsePollingOptions
): { data: T | null; loading: boolean; error: Error | null } => {
  const { interval, immediate = true } = options
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<Error | null>(null)
  const savedFetchFn = useRef(fetchFn)

  useEffect(() => {
    savedFetchFn.current = fetchFn
  }, [fetchFn])

  useEffect(() => {
    let isMounted = true
    let timeoutId: NodeJS.Timeout

    const fetchData = async () => {
      if (!isMounted) return

      setLoading(true)
      try {
        const result = await savedFetchFn.current()
        if (isMounted) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (isMounted) {
          setError(err as Error)
          setData(null)
        }
      } finally {
        if (isMounted) {
          setLoading(false)
        }
      }
    }

    if (immediate) {
      fetchData()
    }

    timeoutId = setInterval(fetchData, interval)

    return () => {
      isMounted = false
      clearInterval(timeoutId)
    }
  }, [interval, immediate])

  return { data, loading, error }
}

export default usePolling