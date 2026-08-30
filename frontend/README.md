# Rug Pull Detector Frontend

This is the React + TypeScript frontend for the Real-Time Crypto Rug-Pull Detection Engine.

## Features

- **Token Page**: View detailed information about any ERC-20 token including risk score, category breakdown, holder distribution, liquidity information, and recent events
- **Wallet Page**: Analyze wallet activity including deployed tokens, transaction history, and relationship graphs
- **Live Feed**: Real-time stream of newly detected tokens and important events
- **Responsive Design**: Works on desktop and mobile devices
- **Automatic Refresh**: Data updates every 2-3 seconds as specified in the MVP

## Getting Started

### Prerequisites

- Node.js 18+ 
- npm or yarn
- The backend API running on http://localhost:8000 (see backend README for setup instructions)

### Installation

1. Clone the repository
2. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
3. Install dependencies:
   ```bash
   npm install
   ```

### Running the Application

1. Make sure the backend is running:
   ```bash
   # In the backend directory
   uvicorn app.main:app --reload
   ```

2. Start the frontend development server:
   ```bash
   npm run dev
   ```

3. Open your browser to http://localhost:5173

## Project Structure

```
src/
├── components/     # Reusable UI components
│   ├── charts/     # Data visualization components (Recharts)
│   ├── layout/     # Page layout components
│   └── ui/         # Primitive UI components (buttons, badges, etc.)
├── hooks/          # Custom React hooks for data fetching
├── pages/          # Page components (TokenPage, WalletPage, LiveFeed)
├── services/       # API service layer
├── utils/          # Utility functions (formatters, constants)
├── App.tsx         # Main application component
├── main.tsx        # Entry point
└── index.css       # Global styles
```

## API Integration

The frontend consumes the following backend endpoints:

- `GET /tokens/{address}` - Token basic information
- `GET /tokens/{address}/risk` - Risk score and category breakdown
- `GET /tokens/{address}/holders` - Holder analysis and distribution
- `GET /tokens/{address}/liquidity` - Liquidity pool information
- `GET /tokens/{address}/pools` - Liquidty pools for a token
- `GET /pools/{pool_address}/events` - Liquidity events for a pool
- `GET /wallets/{address}` - Wallet basic information
- `GET /wallets/{address}/relationships` - Wallet transaction relationships
- `GET /tokens/{address}/wallets` - Wallets associated with a token
- `GET /addresses/{address}/classification` - Address type classification
- `GET /deployers/{address}/analysis` - Deployer history and risk analysis
- `GET /tokens/recent` - Recently detected tokens (for live feed)

## Design Choices

- **React 18** with **TypeScript** for type safety and maintainability
- **Vite** for fast development builds and hot module replacement
- **Recharts** for data visualization as specified in the MVP
- **Axios** for HTTP requests to the backend API
- **React Router DOM v6** for client-side routing
- **Custom hooks** for data fetching and polling logic
- **Responsive design** with mobile-first approach

## Custom Hooks

- `useApi.ts` - Base API call handler with loading/error states
- `usePolling.ts` - Automatic data refresh with configurable intervals
- `useTokenData.ts` - Aggregates token, risk, holders, liquidity, and pool data
- `useWalletData.ts` - Combines wallet info and relationships
- `useDeployerAnalysis.ts` - Fetches deployer history and risk analysis
- `useTokenLiquidityEvents.ts` - Gets liquidity events for a token's pools
- `useAddressClassification.ts` - Gets address type (EOA, contract, pool, etc.)

## Components

### UI Components
- `RiskScoreBadge` - Color-coded circular risk score display
- `AddressDisplay` - Truncated address display with tooltip
- `LoadingSpinner` - Loading state indicator
- `ErrorDisplay` - Error state with retry option

### Chart Components
- `HolderChart` - Pie chart showing holder distribution percentages
- `LiquidityChart` - Bar chart showing token vs pair reserves

### Layout Components
- `Header` - Application header with navigation

### Pages
- `TokenPage` - Detailed view of a specific token
- `WalletPage` - Analysis of a specific wallet
- `LiveFeed` - Real-time stream of token detections and events

## Environment Variables

Create a `.env` file in the frontend directory:

```
VITE_API_URL=http://localhost:8000
```

## Deployment

To build for production:

```bash
npm run build
```

The output will be in the `dist/` directory, which can be served by any static file host.

## Development Guidelines

- Follow the existing code style and patterns
- Keep components small and focused
- Use TypeScript interfaces for API responses
- Handle loading and error states gracefully
- Make components reusable when possible
- Write meaningful commit messages