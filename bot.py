"""
GODMODE v5.2 - FINAL VERSION
Bot Auto MEV menggunakan EIP-7702 + Multicall3
Fitur:
- Scan recent blocks untuk mencari wallet yang delegate ke Multicall3
- Multi Chain Support (ETH, BSC, Base, dll)
- Auto Discovery via txnAuthList (EIP-7702)
"""

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional
from web3 import AsyncWeb3, Web3
from eth_account import Account

# ==================== CONFIG ====================
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
EIP7702_PREFIX = bytes.fromhex("ef0100")

# RPC untuk berbagai jaringan
RPC_FALLBACKS = {
    1: ["https://eth.llamarpc.com", "https://ethereum-rpc.publicnode.com"],
    56: ["https://bsc-dataseed.binance.org", "https://bsc-rpc.publicnode.com"],
    8453: ["https://mainnet.base.org", "https://base.llamarpc.com", "https://base-rpc.publicnode.com"],
    137: ["https://polygon-rpc.com"],
    42161: ["https://arb1.arbitrum.io/rpc"],
    10: ["https://mainnet.optimism.io"],
}

# Token yang akan di-scan (bisa ditambah)
TOKENS_PER_CHAIN = {
    1: ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "0xdAC17F958D2ee523a2206206994597C13D831ec7"],
    56: ["0x55d398326f99059fF775485246999027B3197955", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"],
    8453: ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"],
    137: [],
    42161: [],
    10: [],
}


@dataclass
class Victim:
    address: str
    chain_id: int
    tokens: List[dict] = field(default_factory=list)
    is_delegated: bool = False
    delegated_to: str = ""


class GodModeFinal:
    def __init__(self, private_key: str):
        self.account = Account.from_key(private_key)
        self.w3: Optional[AsyncWeb3] = None
        self.chain_id: int = 0

    async def connect(self, chain_id: int) -> bool:
        """Connect ke jaringan dengan fallback RPC"""
        for rpc in RPC_FALLBACKS.get(chain_id, []):
            try:
                w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc))
                if await w3.is_connected():
                    self.w3 = w3
                    self.chain_id = chain_id
                    print(f"[+] Connected to chain {chain_id}")
                    return True
            except Exception:
                continue
        print(f"[!] Gagal connect ke chain {chain_id}")
        return False

    async def is_eip7702_delegated(self, address: str) -> tuple[bool, str]:
        """Cek apakah address masih delegate ke Multicall3"""
        try:
            code = await self.w3.eth.get_code(Web3.to_checksum_address(address))
            if len(code) == 23 and code[:3] == EIP7702_PREFIX:
                delegated = Web3.to_checksum_address("0x" + code[3:23].hex())
                return delegated.lower() == MULTICALL3.lower(), delegated
            return False, ""
        except:
            return False, ""

    async def find_delegated_wallets_from_recent_blocks(self, blocks_to_scan: int = 150) -> List[str]:
        """
        Scan recent blocks untuk mencari wallet yang melakukan EIP-7702 delegation
        ke Multicall3 melalui authorizationList
        """
        print(f"\n[*] Scanning {blocks_to_scan} block terakhir untuk EIP-7702 Authorization...")

        latest_block = await self.w3.eth.block_number
        delegated_addresses = set()

        for block_num in range(latest_block - blocks_to_scan + 1, latest_block + 1):
            try:
                block = await self.w3.eth.get_block(block_num, full_transactions=True)

                for tx in block.transactions:
                    # Cek transaksi tipe 4 (EIP-7702) dan punya authorizationList
                    if getattr(tx, "type", None) == 4 and hasattr(tx, "authorizationList"):
                        for auth in tx.authorizationList:
                            delegate = auth.get("delegate")
                            if delegate and delegate.lower() == MULTICALL3.lower():
                                authority = Web3.to_checksum_address(auth["authority"])
                                delegated_addresses.add(authority)
                                print(f"[+] Ditemukan delegation → {authority}")

            except Exception:
                continue  # Skip block jika error

        return list(delegated_addresses)

    async def scan_wallet(self, address: str) -> Victim:
        """Scan token pada wallet"""
        victim = Victim(address=address, chain_id=self.chain_id)
        is_del, delegated = await self.is_eip7702_delegated(address)
        victim.is_delegated = is_del
        victim.delegated_to = delegated

        if not is_del:
            return victim

        print(f"\n[*] Scanning tokens untuk: {address}")

        for token_addr in TOKENS_PER_CHAIN.get(self.chain_id, []):
            try:
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_addr),
                    abi=self._get_erc20_abi()
                )
                balance = await contract.functions.balanceOf(address).call()
                if balance > 0:
                    symbol = await contract.functions.symbol().call()
                    victim.tokens.append({
                        "address": token_addr,
                        "symbol": symbol,
                        "balance": balance
                    })
                    print(f"    [+] {symbol}: {balance}")
            except:
                pass

        return victim

    def _get_erc20_abi(self):
        return [
            {"name": "balanceOf", "type": "function", "stateMutability": "view",
             "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
            {"name": "symbol", "type": "function", "stateMutability": "view",
             "outputs": [{"name": "", "type": "string"}]},
        ]

    async def run(self):
        print("🔥 GODMODE v5.2 - FINAL (EIP-7702 Auth Scanner)")
        chain_id = int(input("Chain ID (1=ETH, 56=BSC, 8453=Base): "))

        if not await self.connect(chain_id):
            return

        print("\nPilih Mode:")
        print("1. Scan Recent Blocks (Cari delegation otomatis)")
        print("2. Scan Manual Address")

        choice = input("Pilihan (1/2): ").strip()

        if choice == "1":
            blocks = int(input("Scan berapa block terakhir? (default 150): ") or 150)
            delegated_list = await self.find_delegated_wallets_from_recent_blocks(blocks)

            print(f"\n[+] Total ditemukan {len(delegated_list)} wallet yang delegate ke Multicall3")

            for addr in delegated_list:
                victim = await self.scan_wallet(addr)
                if victim.tokens:
                    print(f"\n🎯 Wallet dengan aset: {addr}")
                    for t in victim.tokens:
                        print(f"   - {t['symbol']}: {t['balance']}")

        else:
            address = input("Masukkan address: ").strip()
            victim = await self.scan_wallet(address)
            print(victim)


if __name__ == "__main__":
    private_key = input("Masukkan Private Key: ").strip()
    bot = GodModeFinal(private_key)
    asyncio.run(bot.run())
