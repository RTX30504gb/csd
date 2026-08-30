import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface HolderChartProps {
  largestHolderPct: number
  top5Pct: number
  top10Pct: number
  top20Pct: number
  creatorHoldingsPct: number
}

const HolderChart: React.FC<HolderChartProps> = ({
  largestHolderPct,
  top5Pct,
  top10Pct,
  top20Pct,
  creatorHoldingsPct
}) => {
  const data = [
    { name: 'Largest Holder', value: largestHolderPct, fill: '#8884d8' },
    { name: 'Top 5 Holders', value: top5Pct - largestHolderPct, fill: '#82ca9d' },
    { name: 'Top 10 Holders', value: top10Pct - top5Pct, fill: '#ffc658' },
    { name: 'Top 20 Holders', value: top20Pct - top10Pct, fill: '#ff7300' },
    { name: 'Creator Holdings', value: creatorHoldingsPct, fill: '#00bfff' },
    { name: 'Other Holders', value: Math.max(0, 100 - top20Pct), fill: '#cccccc' }
  ].filter(item => item.value > 0)

  return (
    <ResponsiveContainer width="100%" height={300}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius="60%"
          outerRadius="80%"
          labelLine={false}
          label={({ name, value, percent }) =>
            `${name}: ${value.toFixed(1)}%`
          }
        >
          {data.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.fill} />
          ))}
        </Pie>
        <Tooltip />
        <Legend
          verticalAlign="top"
          height={36}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}

export default HolderChart