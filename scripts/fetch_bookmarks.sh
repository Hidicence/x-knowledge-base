#!/bin/bash
# fetch_bookmarks.sh - 從 Twitter/X 抓書籤
# 依賴：bird CLI

set -e

# 設定
BIRD_AUTH_TOKEN="${BIRD_AUTH_TOKEN:-6c007455c8c2c103372c76b7f4b69f00f05d1914}"
BIRD_CT0="${BIRD_CT0:-8f669361bac780f12a4fa85c443320cc93dfdf68e9ef0ae3c811085dd103362568cc88c50b004fbffcae1c8dc091bdfd970877b95ab93ed6710bba83717759f857bef42107fbf03cbccfbd504614b782}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-/home/ubuntu/clawd/memory/bookmarks}"

echo "📥 開始抓取書籤..."

# 用 bird 抓書籤
OUTPUT=$(bird --auth-token "$BIRD_AUTH_TOKEN" --ct0 "$BIRD_CT0" bookmarks 2>&1)

# 解析書籤，提取連結和內容
# 格式：🔗 https://x.com/用戶名/status/ID
echo "$OUTPUT" | grep -E "🔗 https://x.com/" | while read -r line; do
    URL=$(echo "$line" | grep -oE "https://x.com/[a-zA-Z0-9_]+/status/[0-9]+")
    if [ -n "$URL" ]; then
        echo "📌 發現書籤: $URL"
        # 這裡只是標記，實際抓取由 fetch_article.sh 處理
        echo "$URL" >> /tmp/new_bookmarks.txt
    fi
done

echo "✅ 書籤列表已擷取"
