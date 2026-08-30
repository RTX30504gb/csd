import { useState, useEffect } from 'react'
import { getRecentTokens } from '../services/api'
import { AddressDisplay } from '../components/ui/AddressDisplay'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorDisplay } from '../components/ui/ErrorDisplay'
import { RiskScoreBadge } from '../components/ui/RiskScoreBadge'

const LiveFeed: React.FC = () => {
  const [tokens, setTokens] = useState<any[]>([])
  const [previousTokens, setPreviousTokens] = useState<string[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<Error | null>(null)
  const [events, setEvents] = useState<Array<{ time: string; description: string }>>([])

  useEffect(() => {
    let isMounted = true
    const fetchTokens = async () => {
      try {
        setLoading(true)
        const response = await getRecentTokens(20)
        const newTokens = response.tokens || []

        if (isMounted) {
          setTokens(newTokens)

          // Detect new tokens and create events
          const newTokenAddresses = newTokens
            .map((t: any) => t.contract_address.toLowerCase())
            .filter((addr: string) => !previousTokens.includes(addr))

          if (newTokenAddresses.length > 0) {
            const newEvents = newTokenAddresses.map(addr => {
              const token = newTokens.find(t => t.contract_address.toLowerCase() === addr)
              const time = new Date().toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
              })
              return {
                time,
                description: `New token detected: ${token?.symbol || 'Unknown'}`
              }
            })

            // Also check for other meaningful events
            const riskUpdateEvents = newTokens
              .filter((t: any) => t.risk_score !== undefined && t.risk_score >= 70) // High risk tokens
              .map((token: any) => {
                const time = new Date().toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                  hour12: false
                })
                return {
                  time,
                  description: `High risk token detected: ${token.symbol} (Risk: ${token.risk_score})`
                }
              })

            const allNewEvents = [...newEvents, ...riskUpdateEvents]

            setEvents(prev => {
              const updated = [...allNewEvents, ...prev]
              return updated.slice(0, 50) // Keep only last 50 events
            })
          }

          setPreviousTokens(newTokens.map(t => t.contract_address.toLowerCase()))
          setLoading(false)
        }
      } catch (err) {
        if (isMounted) {
          setError(err as Error)
          setLoading(false)
        }
      }
    }

    fetchTokens()
    const intervalId = setInterval(fetchTokens, 3000) // Poll every 3 seconds

    return () => {
      isMounted = false
      clearInterval(intervalId)
    }
  }, [previousTokens])

  if (loading) {
    return <LoadingSpinner />
  }

  if (error) {
    return <ErrorDisplay
      message="Failed to load live feed. Please try again."
      onRetry={() => window.location.reload()}
    />
  }

  return (
    <div className="container">
      <div className="live-feed-header">
        <h1>Live Feed</h1>
        <p>Real-time token detection and alerts</p>
      </div>

      <div className="live-feeds-section">
        <h2>Recently Detected Tokens</h2>
        {tokens.length > 0 ? (
          <div className="tokens-list">
            {tokens.map((token: any, index: number) => (
              <div key={index} className="token-item">
                <div className="token-info">
                  <div className="token-name">
                    <strong>{token.name} ({token.symbol})</strong>
                  </div>
                  <div className="token-address">
                    <AddressDisplay address={token.contract_address} />
                  </div>
                </div>
                <div className="token-risk">
                  {token.risk_score !== undefined ? (
                    <>
                      <RiskScoreBadge score={token.risk_score} size={40} />
                      <span className="risk-level">
                        {token.risk_level || 'Unknown'}
                      </span>
                    </>
                  ) : (
                    <span>Risk: Analyzing...</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p>No tokens detected yet.</p>
        )}
      </div>

      <div className="events-section">
        <h2>Activity Events</h2>
        <div className="live-feed-container">
          {events.length > 0 ? (
            events.map((event, index) => (
              <div key={index} className="event-item">
                <div className="event-time">[{event.time}]</div>
                <div className="event-description">{event.description}</div>
              </div>
            ))
          ) : (
            <p>No recent activity.</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default LiveFeed