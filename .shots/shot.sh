#!/bin/bash
# 用无头 Chrome 截图：./shot.sh <名称> <路径> [宽] [高]
set -u
NAME=$1
URL_PATH=$2
W=${3:-1560}
H=${4:-1500}
DIR=/tmp/chan_shots
mkdir -p "$DIR"
rm -f "$DIR/$NAME.png"
C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$C" --headless=new --disable-gpu --hide-scrollbars --window-size=$W,$H --virtual-time-budget=12000 \
  --user-data-dir="$DIR/p_$NAME" --screenshot="$DIR/$NAME.png" "http://localhost:${PORT:-5173}$URL_PATH" >/dev/null 2>&1 &
PID=$!
for _ in $(seq 1 45); do
  [ -f "$DIR/$NAME.png" ] && break
  sleep 1
done
sleep 1
kill $PID 2>/dev/null
ls -la "$DIR/$NAME.png"
