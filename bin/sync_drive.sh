#!/usr/bin/env bash
set -euo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# What to sync:
#  - logs/ and summaries/ (results)
#  - docs/ and rulebooks (optional)
#  - CSVs and analyzer
rclone sync "$SRC_DIR/logs"       "gdrive:Scarecrovv/logs"       --create-empty-src-dirs
rclone sync "$SRC_DIR/summaries"  "gdrive:Scarecrovv/summaries"  --create-empty-src-dirs
rclone copy "$SRC_DIR/cards.csv"  "gdrive:Scarecrovv/"
rclone copy "$SRC_DIR/globals.csv" "gdrive:Scarecrovv/"
# Optional: copy your Markdown rulebooks
[ -f "$SRC_DIR/Scarecrovvs_Realm_Rulebooks.md" ] && rclone copy "$SRC_DIR/Scarecrovvs_Realm_Rulebooks.md" "gdrive:Scarecrovv/"
[ -f "$SRC_DIR/summaries/README_summary_v5_1.md" ] && rclone copy "$SRC_DIR/summaries/README_summary_v5_1.md" "gdrive:Scarecrovv/"
echo "✓ Synced to Google Drive (Scarecrovv/)"
