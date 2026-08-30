import { useParams } from 'react-router-dom'
import { useWalletData } from '../hooks/useWalletData'
import { useDeployerAnalysis } from '../hooks/useDeployerAnalysis'
import { AddressDisplay } from '../components/ui/AddressDisplay'
import { LoadingSpinner } from '../components/ui/LoadingSpinner'
import { ErrorDisplay } from '../components/ui/ErrorDisplay'
import { RiskScoreBadge } from '../components/ui/RiskScoreBadge'

const WalletPage: React.FC = () => {
  const { address } = useParams<{ address: string }>()
  const normalizedAddress = address?.toLowerCase() || ''

  const {
    data: walletData,
    loading: walletLoading,
    error: walletError
  } = useWalletData(normalizedAddress)

  const {
    data: deployerData,
    loading: deployerLoading,
    error: deployerError
  } = useDeployerAnalysis(normalizedAddress)

  if (
    walletLoading ||
    deployerLoading
  ) {
    return <LoadingSpinner />
  }

  if (
    walletError ||
    deployerError
  ) {
    return <ErrorDisplay
      message="Failed to load wallet data. Please try again."
      onRetry={() => window.location.reload()}
    />
  }

  if (!walletData.wallet) {
    return <ErrorDisplay message="Wallet not found" />
  }

  const wallet = walletData.wallet
  const deployer = deployerData || {}
  const relationships = walletData.relationships || {
    outgoing_count: 0,
    incoming_count: 0,
    outgoing: [],
    incoming: []
  }

  return (
    <div className="container">
      <div className="wallet-header">
        <h1>Wallet Address</h1>
        <div className="address-section">
          <span>Address:</span>
          <AddressDisplay address={wallet.address} />
        </div>
      </div>

      <div className="wallet-info-grid">
        <div className="info-item">
          <h3>Reputation Score</h3>
          <div className="reputation-score">
            {deployer.risk_score !== undefined ? (
              <>
                <RiskScoreBadge score={deployer.risk_score || 0} size={60} />
                <div className="reputation-text">
                  <div className="reputation-level">
                    {deployer.risk_level || 'Unknown'}
                  </div>
                  <div>Score: {deployer.risk_score || 0}/100</div>
                </div>
              </>
            ) : (
              <div>Reputation score not available</div>
            )}
          </div>
        </div>

        <div className="info-item">
          <h3>Activity Statistics</h3>
          <div className="info-grid">
            <div>
              <strong>Tokens Deployed:</strong> {wallet.tokens_deployed || 0}
            </div>
            <div>
              <strong>Previous Launches:</strong> {deployer.previous_launches || 0}
            </div>
            <div>
              <strong>Suspicious Launches:</strong> {deployer.suspicious_launches || 0}
            </div>
            <div>
              <strong>Liquidity Withdrawals:</strong> {deployer.liquidity_withdrawals || 0}
            </div>
            <div>
              <strong>Tokens as Pool:</strong> {wallet.tokens_as_pool || 0}
            </div>
            <div>
              <strong>Tokens as Transfer:</strong> {wallet.tokens_as_transfer || 0}
            </div>
            <div>
              <strong>First Seen Block:</strong> {wallet.first_seen_block || 'Unknown'}
            </div>
            <div>
              <strong>Last Seen Block:</strong> {wallet.last_seen_block || 'Unknown'}
            </div>
            <div>
              <strong>First Seen At:</strong>
              {wallet.first_seen_at ? new Date(wallet.first_seen_at).toLocaleString() : 'Unknown'}
            </div>
            <div>
              <strong>Last Seen At:</strong>
              {wallet.last_seen_at ? new Date(wallet.last_seen_at).toLocaleString() : 'Unknown'}
            </div>
          </div>
        </div>

        <div className="info-item">
          <h3>Wallet Relationships</h3>
          <p>
            This wallet has {relationships.outgoing_count} outgoing and
            {relationships.incoming_count} incoming relationships.
          </p>

          {(relationships.outgoing_count > 0 || relationships.incoming_count > 0) && (
            <>
              {relationships.outgoing_count > 0 && (
                <>
                  <h4>Outgoing Relationships:</h4>
                  <div className="relationships-list">
                    {relationships.outgoing.map((rel: any, index: number) => (
                      <div key={index} className="relationship-item">
                        <div className="relationship-rel">
                          {rel.kind.replace(/_/g, ' ').toUpperCase()}
                        </div>
                        <div className="relationship-arrow">→</div>
                        <div className="relationship-address">
                          <AddressDisplay address={rel.b} />
                        </div>
                        <div className="relationship-weight">
                          Weight: {rel.weight}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {relationships.incoming_count > 0 && (
                <>
                  <h4>Incoming Relationships:</h4>
                  <div className="relationships-list">
                    {relationships.incoming.map((rel: any, index: number) => (
                      <div key={index} className="relationship-item">
                        <div className="relationship-address">
                          <AddressDisplay address={rel.a} />
                        </div>
                        <div className="relationship-arrow">←</div>
                        <div className="relationship-rel">
                          {rel.kind.replace(/_/g, ' ').toUpperCase()}
                        </div>
                        <div className="relationship-weight">
                          Weight: {rel.weight}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}

          {(relationships.outgoing_count === 0 && relationships.incoming_count === 0) && (
            <p>No relationships found for this wallet.</p>
          )}
        </div>

        {/* Recent Tokens Deployed by this Wallet */}
        <div className="info-item">
          <h3>Recently Deployed Tokens</h3>
          {deployer.recently_deployed_tokens && deployer.recently_deployed_tokens.length > 0 ? (
            <div className="recent-tokens-list">
              {deployer.recently_deployed_tokens.map((token: any, index: number) => (
                <div key={index} className="recent-token-item">
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
                        <RiskScoreBadge score={token.risk_score} size={30} />
                        <span className="risk-level-small">
                          {token.risk_level || 'Unknown'}
                        </span>
                      </>
                    ) : (
                      <span className="risk-analyzing">Analyzing...</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p>No recently deployed tokens found.</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default WalletPage