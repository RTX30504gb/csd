import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { TokenPage } from './pages/TokenPage'
import { WalletPage } from './pages/WalletPage'
import { LiveFeed } from './pages/LiveFeed'
import { Header } from './components/layout/Header'

function App() {
  return (
    <BrowserRouter>
      <Header />
      <Routes>
        <Route path="/" element={<Navigate to="/live" replace />} />
        <Route path="/token/:address" element={<TokenPage />} />
        <Route path="/wallet/:address" element={<WalletPage />} />
        <Route path="/live" element={<LiveFeed />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App