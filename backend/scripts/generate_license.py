#!/usr/bin/env python3
"""
License Generator for Tianjun AI Vision System

Usage:
    python generate_license.py --machine-id TJ-A3F8B2C1D4E5 --expires 2027-12-31 --customer "某某公司"
    python generate_license.py --machine-id TJ-A3F8B2C1D4E5 --permanent --customer "某某公司"

The private key (private.pem) must be in the same directory or specified via --key.
Output: a .lic file that the customer imports into the application.
"""

import argparse
import json
import base64
import os
import sys

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    print("ERROR: 'cryptography' package not installed.")
    print("Run: pip install cryptography")
    sys.exit(1)


def generate_license(machine_id: str, expires_at: str, customer_name: str, private_key_path: str, output_path: str):
    with open(private_key_path, 'rb') as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    payload = {
        "machineId": machine_id,
        "customerName": customer_name,
    }
    if expires_at:
        payload["expiresAt"] = expires_at

    data_str = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))

    signature = private_key.sign(
        data_str.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    sig_b64 = base64.b64encode(signature).decode('utf-8')

    license_obj = {
        "data": data_str,
        "signature": sig_b64
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(license_obj, f, ensure_ascii=False, indent=2)

    print(f"License generated: {output_path}")
    print(f"  Machine ID : {machine_id}")
    print(f"  Customer   : {customer_name}")
    print(f"  Expires    : {expires_at or 'permanent'}")


def main():
    parser = argparse.ArgumentParser(description='Generate license file for Tianjun AI Vision System')
    parser.add_argument('--machine-id', required=True, help='Machine ID (e.g. TJ-A3F8B2C1D4E5)')
    parser.add_argument('--customer', required=True, help='Customer name')
    parser.add_argument('--expires', default=None, help='Expiration date (YYYY-MM-DD), omit for permanent')
    parser.add_argument('--permanent', action='store_true', help='No expiration')
    parser.add_argument('--key', default=None, help='Path to private key (default: keys/private.pem)')
    parser.add_argument('--output', '-o', default=None, help='Output .lic file path')

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))

    key_path = args.key or os.path.join(project_root, 'keys', 'private.pem')
    if not os.path.exists(key_path):
        print(f"ERROR: Private key not found at {key_path}")
        sys.exit(1)

    expires = None if args.permanent else args.expires

    output = args.output or f"license_{args.machine_id}.lic"

    generate_license(args.machine_id, expires, args.customer, key_path, output)


if __name__ == '__main__':
    main()
