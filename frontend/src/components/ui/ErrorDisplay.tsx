interface ErrorDisplayProps {
  message: string
  onRetry?: () => void
}

const ErrorDisplay: React.FC<ErrorDisplayProps> = ({ message, onRetry }) => {
  return (
    <div className="error">
      <h3>Error</h3>
      <p>{message}</p>
      {onRetry && (
        <button onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export default ErrorDisplay