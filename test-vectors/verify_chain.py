#!/usr/bin/env python3
"""
Reference Verifier for financial.clearing.v2.NovationEvent Test Vectors.

Verifies:
1. Per-contract SHA-256 hash-chain continuity across OP_RETURN_DATA and STAS_PERMISSIONED outputs.
2. Protobuf wire deserialization (compile novation_event.proto with protoc first).
3. Selective auditor decryption of EncryptedBlob (AES-256-GCM) via derived per-event CEKs.
4. Cryptographic binding between decrypted canonical CDM JSON (RFC 8785) and commitment_sha256.

Usage:
  protoc -I=proto --python_out=. proto/financial/clearing/v2/novation_event.proto
  python3 test-vectors/verify_chain.py
"""

import hashlib
import hmac
import json
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("Install cryptography: pip install cryptography protobuf")

try:
    from financial.clearing.v2 import novation_event_pb2
except ImportError:
    sys.exit(
        "Compile proto first:\n"
        "  protoc -I=proto --python_out=. proto/financial/clearing/v2/novation_event.proto"
    )


def main() -> None:
    vec_path = Path(__file__).parent / "novation_lifecycle_vectors.json"
    data = json.loads(vec_path.read_text(encoding="utf-8"))

    master_key = bytes.fromhex(data["test_master_key_hex"])
    expected_prev_hash = "00" * 32

    print(f"Verifying contract lifecycle: {data['contract_id']}\n")

    for idx, ev_entry in enumerate(data["events"], start=1):
        raw_wire = bytes.fromhex(ev_entry["wire_hex"])
        assert len(raw_wire) == ev_entry["wire_byte_length"], "Wire length mismatch"

        # 1. Verify hash-chain continuity over exact broadcast wire bytes
        assert ev_entry["previous_event_hash"] == expected_prev_hash, "Chain break"

        # 2. Parse Proto3 wire payload
        msg = novation_event_pb2.NovationEvent()
        msg.ParseFromString(raw_wire)
        assert msg.schema_version == 2
        assert msg.cdm_lifecycle_event.sequence == idx
        assert msg.cdm_lifecycle_event.previous_event_hash.hex() == expected_prev_hash

        # 3. Derive per-event Content Encryption Key (CEK) & decrypt AEAD envelope
        sealed = msg.destination_mapping.data_ledger.data_payload.sealed_metadata
        cek = hmac.new(master_key, sealed.key_id, hashlib.sha256).digest()
        aad = ev_entry["associated_data_utf8"].encode("utf-8")
        assert hashlib.sha256(aad).digest() == sealed.associated_data_hash, "AAD hash mismatch"

        plaintext = AESGCM(cek).decrypt(sealed.nonce, sealed.ciphertext, aad)

        # 4. Verify decrypted canonical CDM record matches on-chain commitment_sha256
        commitment = msg.destination_mapping.data_ledger.data_payload.commitment_sha256
        assert hashlib.sha256(plaintext).digest() == commitment, "CDM commitment mismatch"
        assert plaintext.decode("utf-8") == ev_entry["canonical_cdm_jcs"]

        wire_sha256 = hashlib.sha256(raw_wire).hexdigest()
        assert wire_sha256 == ev_entry["wire_sha256"]

        print(
            f"[OK] Seq {idx} | {ev_entry['event_type']:<10} | "
            f"{ev_entry['script_type']:<17} | {len(raw_wire)} B | "
            f"wire_sha256={wire_sha256[:16]}..."
        )
        expected_prev_hash = wire_sha256

    print("\nAll 4 lifecycle events, hash-chain links, and AEAD commitments verified!")


if __name__ == "__main__":
    main()
