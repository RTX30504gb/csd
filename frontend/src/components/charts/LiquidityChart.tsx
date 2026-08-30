import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface LiquidityChartProps {
  reserveToken: string | null
  reservePair: string | null
}

const LiquidityChart: React.FC<LiquidityChartProps> = ({
  reserveToken,
  reservePair
}) => {
  // For MVP, we'll show a simple bar chart of current reserves
  // In future, this could show historical data
  const tokenAmount = reserveToken ? parseFloat(reserveToken) : 0
  const pairAmount = reservePair ? parseFloat(reservePair) : 0

  const data = [
    { name: 'Token Reserve', value: tokenAmount > 0 ? 100 : 0, fill: '#8884d8' },
    { name: 'Pair Reserve', value: pairAmount > 0 ? 100 : 0, fill: '#82ca9d' }
  ].filter(item => item.value > 0)

  if (data.length === 0) {
    return (
      <div className="chart-container">
        <p>No liquidity data available</p>
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data}>
        <XAxis dataKey="name" />
        <YAxis />
        <Tooltip />
        <Legend />
        <Bar dataKey="value" fill="#8884d8" />
      </BarChart>
    </ResponsiveContainer>
  )
}

export default LiquidityChart