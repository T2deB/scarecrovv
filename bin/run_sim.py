#!/usr/bin/env python3
from scarecrovv.config import build_config_from_cli
from scarecrovv.engine.loop import run_many
import json

if __name__ == "__main__":
    cfg, args = build_config_from_cli()
    out = run_many(cfg, games=args.games)  # <-- pass games explicitly
    print(json.dumps(out, indent=2))
