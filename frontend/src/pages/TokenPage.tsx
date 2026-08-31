import { useParams } from 'react-router-dom'
import { useTokenData } from '../hooks/useTokenData'
import { useAddressClassification } from '../hooks/useAddressClassification'
import { useTokenLiquidityEvents } from '../hooks/useTokenLiquidityEvents'
import { RiskScoreBadge } from '../components/ui/RiskScoreBadge'
import { AddressDisplay } from '../components/ui/AddressDisplay'
import { HolderChart } from '../components/charts/HolderChart'
import { LiquidityChart } from '../components/charts/LiquidityChart'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorDisplay } from '../components/ui/ErrorDisplay'
import { formatNumber } from '../utils/formatters'

const TokenPage: React.FC = () => {
  const { address } = useParams<{ address: string }>()
  const normalizedAddress = address?.toLowerCase() || ''

  const {
    data: tokenData,
    loading: tokenLoading,
    error: tokenError
  } = useTokenData(normalizedAddress)

  const {
    data: classificationData,
    loading: classificationLoading,
    error: classificationError
  } = useAddressClassification(normalizedAddress)

  const {
    data: liquidityEventsData,
    loading: liquidityEventsLoading,
    error: liquidityEventsError
  } = useTokenLiquidityEvents(normalizedAddress)

  if (
    tokenLoading ||
    classificationLoading ||
    liquidityEventsLoading
  ) {
    return <LoadingSpinner />
  }

  if (
    tokenError ||
    classificationError ||
    liquidityEventsError
  ) {
    return <ErrorDisplay
      message="Failed to load token data. Please try again."
      onRetry={() => window.location.reload()}
    />
  }

  if (!tokenData.token) {
    return <ErrorDisplay message="Token not found" />
  }

  const token = tokenData.token
  const risk = tokenData.risk || { analyzed: false }
  const holders = tokenData.holders || { analyzed: false }
  const liquidity = tokenData.liquidity || {}
  const pools = tokenData.pools || { pools: [] }
  const classification = classificationData || {}
  const liquidityEvents = liquidityEventsData || { events: [] }

  return (
    <div className="container">
      <div className="token-header">
        <h1>
          {token.name} ({token.symbol})
        </h1>
        <div className="address-section">
          <span>Contract Address:</span>
          <AddressDisplay address={token.contract_address} />
        </div>
      </div>

      <div className="token-info-grid">
        <div className="info-item">
          <h3>Risk Score</h3>
          <div className="risk-score">
            {risk.analyzed ? (
              <>
                <RiskScoreBadge score={risk.risk_score || 0} />
                <div className="risk-score-text">
                  <div className="risk-score-level">
                    {risk.risk_level || 'Unknown'}
                  </div>
                  <div>Score: {risk.risk_score || 0}/100</div>
                </div>
              </>
            ) : (
              <div>Risk analysis not available</div>
            )}
          </div>
        </div>

        <div className="info-item">
          <h3>Category Breakdown</h3>
          <div className="category-breakdown">
            {risk.analyzed && risk.category_scores ? (
              <>
                <div className="category-item">
                  <div className="category-label">Contract</div>
                  <div className="category-value">
                    {risk.category_scores.contract || 0}
                  </div>
                </div>
                <div className="category-item">
                  <div className="category-label">Liquidity</div>
                  <div className="category-value">
                    {risk.category_scores.liquidity || 0}
                  </div>
                </div>
                <div className="category-item">
                  <div className="category-label">Holder</div>
                  <div className="category-value">
                    {risk.category_scores.holder || 0}
                  </div>
                </div>
                <div className="category-item">
                  <div className="category-label">Deployer</div>
                  <div className="category-value">
                    {risk.category_scores.deployer || 0}
                  </div>
                </div>
                <div className="category-item">
                  <div className="category-label">Behavior</div>
                  <div className="category-value">
                    {risk.category_scores.behavior || 0}
                  </div>
                </div>
              </>
            ) : (
              <div>Category scores not available</div>
            )}
          </div>
        </div>

        <div className="info-item">
          <h3>Reasons</h3>
          <div className="reasons-list">
            {risk.analyzed ? (
              <>
                <h3>Risk Factors:</h3>
                {risk.reasons && risk.reasons.length > 0 ? (
                  <ul>
                    {risk.reasons.map((reason: string, index: number) => (
                      <li key={index}>{reason}</li>
                    ))}
                  </ul>
                ) : (
                  <p>No specific risk factors identified</p>
                )}
              </>
            ) : (
              <p>Risk analysis not available</p>
            )}
          </div>
        </div>
      </div>

      <div className="token-details">
        <div className="details-section">
          <h3>Token Information</h3>
          <div className="info-grid">
            <div>
              <strong>Name:</strong> {token.name || 'Unknown'}
            </div>
            <div>
              <strong>Symbol:</strong> {token.symbol || 'Unknown'}
            </div>
            <div>
              <strong>Decimals:</strong> {token.decimals || 0}
            </div>
            <div>
              <strong>Total Supply:</strong>
              {token.total_supply ? (
                `${formatNumber(token.total_supply)} ${token.symbol ?? ''}`
              ) : (
                'Unknown'
              )}
            </div>
            <div>
              <strong>Deployer:</strong>
              <AddressDisplay address={token.deployer || ''} />
            </div>
            <div>
              <strong>Creation Block:</strong> {token.creation_block || 'Unknown'}
            </div>
            <div>
              <strong>Detected At:</strong>
              {token.detected_at ? new Date(token.detected_at).toLocaleString() : 'Unknown'}
            </div>
          </div>
        </div>

        <div className="details-section">
          <h3>Holder Distribution</h3>
          {holders.analyzed ? (
            <>
              <div className="info-grid">
                <div>
                  <strong>Largest Holder:</strong>
                  {(holders.largest_holder_pct || 0).toFixed(2)}%
                </div>
                <div>
                  <strong>Top 5 Holders:</strong>
                  {(holders.top5_pct || 0).toFixed(2)}%
                </div>
                <div>
                  <strong>Top 10 Holders:</strong>
                  {(holders.top10_pct || 0).toFixed(2)}%
                </div>
                <div>
                  <strong>Top 20 Holders:</strong>
                  {(holders.top20_pct || 0).toFixed(2)}%
                </div>
                <div>
                  <strong>Creator Holdings:</strong>
                  {(holders.creator_holdings_pct || 0).toFixed(2)}%
                </div>
                <div>
                  <strong>Creator Associated Holdings:</strong>
                  {(holders.creator_associated_holdings_pct || 0).toFixed(2)}%
                </div>
              </div>
              <HolderChart
                largestHolderPct={holders.largest_holder_pct || 0}
                top5Pct={holders.top5_pct || 0}
                top10Pct={holders.top10_pct || 0}
                top20Pct={holders.top20_pct || 0}
                creatorHoldingsPct={holders.creator_holdings_pct || 0}
              />
            </>
          ) : (
            <p>Holder analysis not available</p>
          )}
        </div>

        <div className="details-section">
          <h3>Liquidity Information</h3>
          {Object.keys(liquidity).length > 0 ? (
            <>
              <div className="info-grid">
                <div>
                  <strong>Pool Address:</strong>
                  <AddressDisplay address={liquidity.pool_address || ''} />
                </div>
                <div>
                  <strong>DEX:</strong> {liquidity.dex || 'Unknown'}
                </div>
                <div>
                  <strong>Pair Asset:</strong> {liquidity.pair_asset || 'Unknown'}
                </div>
                <div>
                  <strong>Reserve Token:</strong> {formatNumber(liquidity.reserve_token)} {token.symbol || ''}
                </div>
                <div>
                  <strong>Reserve Pair:</strong> {formatNumber(liquidity.reserve_pair)} {liquidity.pair_asset || ''}
                </div>
                <div>
                  <strong>Discovered Block:</strong> {liquidity.discovered_block || 'Unknown'}
                </div>
              </div>
              <LiquidityChart
                reserveToken={liquidity.reserve_token}
                reservePair={liquidity.reserve_pair}
              />
            </>
          ) : (
            <p>No liquidity information available</p>
          )}
        </div>

        <div className="details-section">
          <h3>Liquidity Pools</h3>
          {pools.pools && pools.pools.length > 0 ? (
            <>
              <p>Found {pools.pools.length} liquidity pool(s):</p>
              <div className="pools-list">
                {pools.pools.map((pool: any, index: number) => (
                  <div key={index} className="pool-item">
                    <strong>Pool {index + 1}:</strong>
                    <AddressDisplay address={pool.pool_address} />
                    <br />
                    DEX: {pool.dex || 'Unknown'} |
                    Pair: {pool.pair_asset || 'Unknown'} |
                    Reserves:{' '}
                    {`${pool.reserve_token ? formatNumber(pool.reserve_token) : '0'} ${token.symbol} / ${pool.reserve_pair ? formatNumber(pool.reserve_pair) : '0'} ${pool.pair_asset || ''}`}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p>No liquidity pools found</p>
          )}
        </div>

        <div className="details-section">
          <h3>Recent Events</h3>
          {liquidityEvents.events && liquidityEvents.events.length > 0 ? (
            <div className="events-list">
              {liquidityEvents.events.map((event: any, index: number) => (
                <div key={index} className="event-item">
                  <div className="event-time">[{new Date(event.detected_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'})}]</div>
                  <div className="event-description">
                    {event.event_type === 'ADD'
                      ? 'Liquidity Added'
                      : event.event_type === 'REMOVE'
                      ? 'Liquidity Removed'
                      : event.event_type}
                  </div>
                  <div className="event-details">
                    Change: {formatNumber(event.value_before)} → {formatNumber(event.value_after)} ({event.percent_change}%)
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p>No recent liquidity events for this token's pools.</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default TokenPage
