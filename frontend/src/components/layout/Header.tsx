import { Link } from 'react-router-dom'

const Header: React.FC = () => {
  return (
    <header className="header">
      <div className="container">
        <h1>
          <Link to="/live" style={{ color: 'inherit', textDecoration: 'none' }}>
            Rug Pull Detector
          </Link>
        </h1>
      </div>
    </header>
  )
}

export default Header