#!/usr/bin/env bash
# 下载币安 U 本位永续 1 分钟 K 线（data.binance.vision 公开数据，不需要 Key），转成紧凑格式长期保存。
#
#   用法（建议放后台，断了重跑会跳过已完成的币）：
#     nohup bash research/data/fetch_um_1m.sh > research/data/fetch.log 2>&1 &
#     tail -f research/data/fetch.log          # 看进度
#
#   可选环境变量：
#     START=2024-01  END=2026-09   起止月份（含），默认 2024-01 到上个月
#     JOBS=8                        并发下载数
#     OUT=/root/bamy-data/um_1m     输出目录（默认在仓库外，避免进 git）
#
# 结果：$OUT/<SYMBOL>.npz（字段 t,o,h,l,c,qv；t 为分钟级 Unix 秒，压缩保存），每个币一个文件。
# 原始 zip 转完即删。全市场 2024-01 起估计 8–12 GB。已存在的 .npz 会跳过；想补新月份就删掉对应币的 .npz 重跑。
set -uo pipefail   # 不用 -e：单个币失败不能让整批停下
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT:-/root/bamy-data/um_1m}"
START="${START:-2024-01}"
END="${END:-$(date -d "$(date +%Y-%m-01) -1 month" +%Y-%m)}"
JOBS="${JOBS:-8}"
VENV="${VENV:-/root/bamy-data/venv}"            # 转换用的 Python 环境（只装 numpy），第一次自动建
PY="${PY:-$VENV/bin/python}"
if ! "$PY" -c "import numpy" 2>/dev/null; then
  echo "[$(date '+%F %T')] 首次运行：在 $VENV 建 Python 环境并安装 numpy…"
  if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
    UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
    "$UV" venv -q "$VENV" && "$UV" pip install -q --python "$VENV/bin/python" "numpy==2.3.4"
  else
    python3 -m venv "$VENV" && "$VENV/bin/pip" install -q "numpy==2.3.4"
  fi
  "$PY" -c "import numpy" || { echo "numpy 安装失败，可手动指定：PY=/path/to/python"; exit 1; }
fi
BASE="https://data.binance.vision/data/futures/um/monthly/klines"
mkdir -p "$OUT/_zip"

echo "[$(date '+%F %T')] 列出全部 U 本位交易对…"
LIST="$OUT/_symbols.txt"
if [ ! -s "$LIST" ]; then
  marker=""
  : > "$LIST.tmp"
  while :; do
    page=$(curl -sf "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/${marker:+&marker=$marker}")
    echo "$page" | grep -o '<Prefix>data/futures/um/monthly/klines/[A-Z0-9]*USDT/</Prefix>' \
      | sed 's|.*klines/||; s|/</Prefix>||' >> "$LIST.tmp"
    echo "$page" | grep -q '<IsTruncated>true</IsTruncated>' || break
    marker=$(echo "$page" | grep -o '<NextMarker>[^<]*</NextMarker>' | sed 's/<[^>]*>//g')
    [ -n "$marker" ] || break
  done
  # 去掉稳定币与带日期的交割合约
  grep -Ev '^(USDC|FDUSD|TUSD|BUSD|DAI|USDP|USDE|BFUSD|RLUSD|USD1)USDT$|_' "$LIST.tmp" | sort -u > "$LIST"
  rm -f "$LIST.tmp"
fi
TOTAL=$(wc -l < "$LIST")
echo "[$(date '+%F %T')] 共 $TOTAL 个交易对，月份 $START → $END，输出 $OUT"

months() {
  local m="$START-01"
  while [ "$(date -d "$m" +%Y-%m)" \< "$END" ] || [ "$(date -d "$m" +%Y-%m)" = "$END" ]; do
    date -d "$m" +%Y-%m
    m=$(date -d "$m +1 month" +%Y-%m-01)
  done
}
MONTHS=$(months | tr '\n' ' ')

one() {   # 下载一个币的全部月份 → 转 npz → 删 zip
  local s="$1" dir="$OUT/_zip/$1"
  [ -f "$OUT/$s.npz" ] && return 0
  mkdir -p "$dir"
  for m in $MONTHS; do
    local f="$dir/$s-1m-$m.zip"
    [ -f "$f" ] || [ -f "$f.404" ] && continue
    curl -sf --retry 3 --retry-delay 2 -o "$f" "$BASE/$s/1m/$s-1m-$m.zip" || { rm -f "$f"; touch "$f.404"; }
  done
  if ls "$dir"/*.zip >/dev/null 2>&1; then
    "$PY" -I "$ROOT/research/data/zip2npz.py" "$dir" "$OUT/$s.npz" && rm -rf "$dir"
  else
    rm -rf "$dir"; echo "$s" >> "$OUT/_empty.txt"   # 该时间段没数据（已下架或太新）
  fi
}
export -f one
export OUT MONTHS BASE PY ROOT

count() { find "$OUT" -maxdepth 1 -name '*.npz' ! -name '*.tmp.npz' | wc -l; }
export -f count
done_before=$(count)
echo "[$(date '+%F %T')] 已完成 $done_before 个，开始下载（并发 $JOBS）"
xargs -P "$JOBS" -I{} bash -c 'one "$1"; echo "[$(date +%T)] $1 完成（累计 $(count)/'"$TOTAL"'）"' _ {} < "$LIST"
echo "[$(date '+%F %T')] 全部完成：$(count) 个币，占用 $(du -sh "$OUT" | cut -f1)"
echo FETCH_DONE
