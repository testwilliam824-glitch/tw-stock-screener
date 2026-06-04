#!/bin/zsh
# 每日全市場選股掃描（本機 launchd 排程呼叫）
# 流程：檢查官方資料是否為「今天」→ 是才跑掃描 → 輸出當日報告 + 桌面通知
set -e
DIR="/Users/william/Desktop/股票"
cd "$DIR" || exit 1
mkdir -p reports
PY=/usr/bin/python3
DATE=$(date +%Y-%m-%d)
OUT="$DIR/reports/${DATE}.txt"

# --- 資料新鮮度檢查：證交所 STOCK_DAY_ALL 的資料日期是否=今天 ---
FRESH=$("$PY" - <<'PYEOF'
import requests, datetime
try:
    r = requests.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL",
                     params={"response":"json"}, headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
    d = str(r.json().get("date",""))            # 期望 YYYYMMDD
    today = datetime.date.today().strftime("%Y%m%d")
    print("FRESH" if d == today else f"STALE:{d}")
except Exception as e:
    print(f"ERR:{e}")
PYEOF
)

if [[ "$FRESH" != "FRESH" ]]; then
    echo "[$DATE] 跳過：官方資料非今日（$FRESH），可能為假日或資料未發布。" >> "$DIR/reports/skip.log"
    exit 0
fi

# --- 執行全市場掃描 ---
echo "===== 台股全市場選股掃描  $DATE =====" > "$OUT"
"$PY" screener2.py --macd-n 3 --months 6 >> "$OUT" 2>&1 || true

# --- 摘要 + 桌面通知 ---
SUMMARY=$(grep -E "全條件命中|日線決選（" "$OUT" | head -2 | tr '\n' ' ')
/usr/bin/osascript -e "display notification \"${SUMMARY:-掃描完成}\" with title \"台股選股 $DATE\" sound name \"Glass\"" 2>/dev/null || true
echo "[$DATE] 完成 -> $OUT" >> "$DIR/reports/run.log"
