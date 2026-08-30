export const formatAddress = (address: string, truncatedLength: number = 6): string => {
  if (!address) return '-'

  const isValidAddress = address.startsWith('0x') && address.length === 42
  if (!isValidAddress) return address

  const start = address.slice(0, truncatedLength + 2) // 0x + truncatedLength
  const end = address.slice(-4)

  return `${start}…${end}`
}

export const formatNumber = (num: string | number): string => {
  if (num === null || num === undefined) return '0'

  const number = typeof num === 'string' ? parseInt(num) : num
  if (isNaN(number)) return '0'

  return number.toLocaleString()
}

export const formatTimestamp = (timestamp: string | Date): string => {
  if (!timestamp) return 'Unknown'

  const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp
  if (isNaN(date.getTime())) return 'Invalid Date'

  return date.toLocaleString()
}

export const formatTimeAgo = (timestamp: string | Date): string => {
  if (!timestamp) return 'Unknown'

  const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp
  if (isNaN(date.getTime())) return 'Invalid Date'

  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSec = Math.round(diffMs / 1000)
  const diffMin = Math.round(diffSec / 60)
  const diffHours = Math.round(diffMin / 60)
  const diffDays = Math.round(diffHours / 24)

  if (diffSec < 5) return 'just now'
  if (diffSec < 60) return `${diffSec}秒前`
  if (diffMin < 60) return `${diffMin}分钟前`
  if (diffHours < 24) return `${diffHours}小时前`
  if (diffDays < 30) return `${diffDays}天前`

  return date.toLocaleDateString()
}

export const getRiskLevelClass = (score: number): string => {
  if (score < 30) return 'risk-score-low'
  if (score < 55) return 'risk-score-suspicious'
  if (score < 80) return 'risk-score-high'
  return 'risk-score-critical'
}

export const getRiskLevelLabel = (score: number): string => {
  if (score < 30) return 'Low'
  if (score < 55) return 'Suspicious'
  if (score < 80) return 'High'
  return 'Critical'
}