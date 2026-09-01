import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';

interface MLMetrics {
  precision: number;
  recall: number;
  f1_score: number;
  auc_roc: number;
  confusion_matrix: number[][];
  sample_count: number;
  error?: string;
}

const fetchMetrics = async (): Promise<MLMetrics> => {
  const res = await fetch('/api/ml/metrics');
  if (!res.ok) throw new Error('Failed to fetch metrics');
  return res.json();
};

export const MLPerformancePage: React.FC = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['ml-metrics'],
    queryFn: fetchMetrics,
    refetchInterval: 10000,
  });

  if (isLoading) return <div className="p-8 text-center">Loading metrics...</div>;
  if (error || data?.error) return <div className="p-8 text-center text-red-500">Error loading metrics: {error?.message || data?.error}</div>;

  if (!data) return null;

  const { precision, recall, f1_score, auc_roc, confusion_matrix, sample_count } = data;

  // Prepare confusion matrix data for BarChart
  // matrix = [[TN, FP], [FN, TP]]
  const cmData = [
    { name: 'True Negative', value: confusion_matrix[0][0], fill: '#10b981' },
    { name: 'False Positive', value: confusion_matrix[0][1], fill: '#ef4444' },
    { name: 'False Negative', value: confusion_matrix[1][0], fill: '#ef4444' },
    { name: 'True Positive', value: confusion_matrix[1][1], fill: '#10b981' },
  ];

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <h1 className="text-3xl font-bold mb-8">ML Model Performance</h1>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-12">
        <MetricCard label="Sample Count" value={sample_count} />
        <MetricCard label="Precision" value={`${(precision * 100).toFixed(1)}%`} />
        <MetricCard label="Recall" value={`${(recall * 100).toFixed(1)}%`} />
        <MetricCard label="AUC-ROC" value={auc_roc.toFixed(3)} />
      </div>

      <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
        <h2 className="text-xl font-semibold mb-6">Confusion Matrix</h2>
        <div className="h-80 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={cmData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="value">
                {cmData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};

const MetricCard: React.FC<{ label: string; value: string | number }> = ({ label, value }) => (
  <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200 text-center">
    <div className="text-sm text-gray-500 uppercase font-medium mb-2">{label}</div>
    <div className="text-2xl font-bold">{value}</div>
  </div>
);
