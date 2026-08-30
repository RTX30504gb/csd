import { getRiskLevelLabel } from '../../utils/formatters'

interface RiskScoreBadgeProps {
  score: number
  size?: number
}

const RiskScoreBadge: React.FC<RiskScoreBadgeProps> = ({ score, size = 80 }) => {
  const riskLevel = getRiskLevelLabel(score)
  const riskLevelLower = riskLevel.toLowerCase()
  const className = `risk-score-circle risk-score-${riskLevelLower}`

  return (
    <div className={className} style={{ width: size, height: size }}>
      {score}
      <span className="risk-score-level">
        {riskLevel}
      </span>
    </div>
  )
}

export default RiskScoreBadge