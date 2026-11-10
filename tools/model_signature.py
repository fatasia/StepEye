"""Sign or verify a model bundle using an external Ed25519 PEM key."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.model_signature import sign_directory, verify_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sign", "verify"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True, help="External PEM private/public key")
    args = parser.parse_args()
    action = sign_directory if args.action == "sign" else verify_directory
    print(json.dumps(action(args.directory, args.key)))


if __name__ == "__main__":
    main()
