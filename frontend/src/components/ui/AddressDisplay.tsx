interface AddressDisplayProps {
  address: string
  truncatedLength?: number
}

const AddressDisplay: React.FC<AddressDisplayProps> = ({ address, truncatedLength = 10 }) => {
  if (!address) return '-'

  const isValidAddress = address.startsWith('0x') && address.length === 42
  if (!isValidAddress) return address

  const start = address.slice(0, truncatedLength + 2) // 0x + truncatedLength
  const end = address.slice(-4)

  return (
    <span className="address-display" title={address}>
      {start}…{end}
    </span>
  )
}

export default AddressDisplay