#!/bin/bash
# Smoke test: exercises the deployed Function URL end-to-end.
# Reads FUNCTION_URL + BEARER_TOKEN from .env.local (gitignored).
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env.local ]]; then
  echo "Missing .env.local — expected FUNCTION_URL=... and BEARER_TOKEN=..." >&2
  exit 1
fi
# shellcheck disable=SC1091
set -a; . .env.local; set +a

: "${FUNCTION_URL:?set FUNCTION_URL in .env.local}"
: "${BEARER_TOKEN:?set BEARER_TOKEN in .env.local}"

URL="${FUNCTION_URL%/}"
H_AUTH="Authorization: Bearer ${BEARER_TOKEN}"
H_BAD="Authorization: Bearer wrong"
H_JSON="Content-Type: application/json"

echo "==> health"
curl -sS -f "$URL/health"; echo

echo "==> 401 without bearer"
curl -sS -o /dev/null -w "  status=%{http_code}\n" -H "$H_BAD" -H "$H_JSON" \
  -d '{"message":"hi"}' "$URL/chat" || true

echo "==> chat (no taste_profile, simple greeting)"
curl -sS -N -H "$H_AUTH" -H "$H_JSON" \
  -d '{"message":"ciao, presentati in una frase"}' "$URL/chat" \
  | grep -E '^(data:|: )' | head -20
echo

echo "==> chat with taste_profile (should trigger search_owned_library)"
PROFILE='{
  "steam":{
    "owned_summary":{"total":3,"by_genre":{"RPG":2,"Strategy":1}},
    "all_owned":[
      {"appid":292030,"name":"The Witcher 3","playtime_hours":184,"genres":["RPG"]},
      {"appid":1086940,"name":"Baldurs Gate 3","playtime_hours":42,"genres":["RPG"]},
      {"appid":281990,"name":"Stellaris","playtime_hours":210,"genres":["Strategy"]}
    ]
  },
  "jellyfin":{"recent_watched":[{"title":"Severance","type":"Series","genres":["Drama","SciFi"]}]}
}'
RESP=$(mktemp)
curl -sS -N -H "$H_AUTH" -H "$H_JSON" \
  -d "{\"message\":\"What RPG should I play tonight, ~2h max?\",\"taste_profile\":$PROFILE}" \
  "$URL/chat" | tee "$RESP" | grep -E '^(data:|: )' | head -40
echo

# Extract the conversation id we just created so we can list/get it
CID=$(grep '"type": "conversation_created"' "$RESP" | head -1 | sed -E 's/.*"conversation_id": "([^"]+)".*/\1/')
echo "==> created conversation_id: $CID"

echo "==> list /conversations"
curl -sS -H "$H_AUTH" "$URL/conversations?limit=5" | python3 -m json.tool
echo

echo "==> get /conversations/$CID (truncated)"
curl -sS -H "$H_AUTH" "$URL/conversations/$CID" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("id:",d["id"],"title:",d["title"],"messages:",len(d["messages"]))
for m in d["messages"]:
    blocks=[]
    for b in m["content"]:
        if "text" in b: blocks.append(("text", b["text"][:60]))
        elif "toolUse" in b: blocks.append(("toolUse", b["toolUse"]["name"]))
        elif "toolResult" in b: blocks.append(("toolResult", b["toolResult"].get("status")))
    print(" ",m["role"],blocks)
'
echo

echo "==> rename, then soft-delete, then verify it moved to trash"
curl -sS -X PATCH -H "$H_AUTH" -H "$H_JSON" -d '{"title":"renamed by smoke"}' \
  "$URL/conversations/$CID" | python3 -m json.tool
curl -sS -X PATCH -H "$H_AUTH" -H "$H_JSON" -d '{"deleted":true}' \
  "$URL/conversations/$CID" | python3 -m json.tool
curl -sS -H "$H_AUTH" "$URL/conversations?trash=true&limit=5" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("trash items:",len(d["items"]),"first:",d["items"][0] if d["items"] else None)
'
echo

echo "==> hard delete"
curl -sS -X DELETE -H "$H_AUTH" "$URL/conversations/$CID" | python3 -m json.tool
echo

rm -f "$RESP"
echo "smoke ok"
