import usePolling from "./usePolling";
import { getAddressClassification } from '../services/api'

interface ClassificationData {
  address: string
  category: string
  [key: string]: any
}

const useAddressClassification = (address: string) => {
  const { data, loading, error } = usePolling(
    () => getAddressClassification(address),
    { interval: 5000 } // Less frequent polling for classification
  )

  return {
    data: data as ClassificationData,
    loading,
    error
  }
}

export default useAddressClassification