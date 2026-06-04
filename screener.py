# -*- coding: utf-8 -*-
"""
多條件技術選股器（台股）
============================================
硬條件（機械式、可精準程式化）：
  ① 股價 10 ~ 100 元
  ③ MACD 柱狀圖剛翻紅（日線：前一根 <=0、最新 > 0）
  ④ 日線「三黑K量縮 → 紅K量微增」
  ⑤ 60分線「三黑K量縮 → 紅K量微增」
軟條件（量化代理、人工確認）：
  ② 頭肩底 / 下降楔形  -> 給 0~100 近似分數，不當硬門檻

輸出：依「符合硬條件數量」與「型態近似分數」排序，
即使沒有完全命中，也會列出最接近的標的（near-miss）。

資料來源：yfinance（免費，有速率限制）。上市 .TW / 上櫃 .TWO 自動切換。
免責：僅為技術面資訊整理，非投資建議。
"""

import os
import time
import argparse
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------- 可調參數 ----------------
PRICE_MIN, PRICE_MAX = 10.0, 100.0      # ① 價格區間
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
VOL_MICRO_MAX = 2.0       # 「量微增」上限：紅K量 / 前一黑K量 <= 此值才算「微增」（避免爆量）
WEDGE_WINDOW = 30         # 下降楔形 / 趨勢觀察窗（日線根數）
HS_WINDOW = 60            # 頭肩底觀察窗（日線根數）
SLEEP_SEC = 0.6           # 每檔間隔，降低被速率限制機率
CACHE_DIR = ".cache"
# ------------------------------------------


def _flatten(df):
    """yfinance 單檔有時回傳 MultiIndex 欄位，壓平成 Open/High/Low/Close/Volume。"""
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def fetch(code, interval, period, use_cache=True):
    """抓單檔資料，先試 .TW（上市）再試 .TWO（上櫃）。日內資料快取當天。"""
    import yfinance as yf
    os.makedirs(CACHE_DIR, exist_ok=True)
    today = pd.Timestamp.now().strftime("%Y%m%d")
    cache_f = os.path.join(CACHE_DIR, f"{code}_{interval}_{today}.pkl")
    if use_cache and os.path.exists(cache_f):
        try:
            return pd.read_pickle(cache_f)
        except Exception:
            pass

    for suffix in (".TW", ".TWO"):
        try:
            df = yf.download(code + suffix, interval=interval, period=period,
                             progress=False, auto_adjust=False, threads=False)
            df = _flatten(df)
            if df is not None and not df.empty and len(df) > 5:
                df = df.dropna(subset=["Close"])
                if use_cache:
                    df.to_pickle(cache_f)
                return df
        except Exception:
            continue
    return pd.DataFrame()


def macd_hist(close):
    """回傳 MACD 柱狀圖 (DIF - DEA) 序列。"""
    ema_f = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_s = close.ewm(span=MACD_SLOW, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return (dif - dea)


def macd_just_turned_red(close):
    """MACD 柱狀圖『剛翻紅』：前一根 <= 0 且最新 > 0。"""
    h = macd_hist(close).dropna()
    if len(h) < 2:
        return False, np.nan, np.nan
    prev, cur = float(h.iloc[-2]), float(h.iloc[-1])
    return (prev <= 0 < cur), prev, cur


def three_black_then_red(df):
    """
    ④/⑤ 型態：最近第 4/3/2 根為黑K（收<開），最新一根為紅K（收>開），
    三黑期間量縮（淨縮：第2根量 < 第4根量），紅K量 > 前一黑K量且為『微增』。
    回傳 (是否符合, 細節 dict)。
    """
    if df is None or len(df) < 4:
        return False, {}
    o = df["Open"].astype(float).values
    c = df["Close"].astype(float).values
    v = df["Volume"].astype(float).values

    b3, b2, b1, last = -4, -3, -2, -1   # 三根黑K + 最新紅K
    down = (c[b3] < o[b3]) and (c[b2] < o[b2]) and (c[b1] < o[b1])
    up = c[last] > o[last]

    vol_shrink_strict = v[b1] <= v[b2] <= v[b3]      # 嚴格遞減
    vol_shrink_net = v[b1] < v[b3]                    # 淨縮（較寬鬆）
    ratio = (v[last] / v[b1]) if v[b1] > 0 else np.nan
    vol_up = v[last] > v[b1]
    vol_micro = vol_up and (ratio <= VOL_MICRO_MAX)   # 微增（非爆量）

    passed = bool(down and up and vol_shrink_net and vol_micro)
    detail = {
        "three_black": bool(down),
        "last_red": bool(up),
        "vol_shrink_strict": bool(vol_shrink_strict),
        "vol_shrink_net": bool(vol_shrink_net),
        "vol_up": bool(vol_up),
        "vol_ratio_redK": round(float(ratio), 2) if ratio == ratio else None,
    }
    return passed, detail


def _local_extrema(arr, order=2):
    """簡易局部極值：兩側 order 根都高/低於自己。回傳 (谷index, 峰index)。"""
    troughs, peaks = [], []
    for i in range(order, len(arr) - order):
        win = arr[i - order:i + order + 1]
        if arr[i] == win.min():
            troughs.append(i)
        if arr[i] == win.max():
            peaks.append(i)
    return troughs, peaks


def falling_wedge_score(df):
    """
    下降楔形量化代理（0~100）：
      - 前段為下跌趨勢（窗內高點、低點皆走低 -> 斜率為負）
      - 區間收斂（後半段振幅 < 前半段振幅）
      - 最新價接近上緣（醞釀突破）
    這是『近似』，需人工看圖確認。
    """
    if df is None or len(df) < WEDGE_WINDOW:
        return 0.0
    w = df.tail(WEDGE_WINDOW)
    high = w["High"].astype(float).values
    low = w["Low"].astype(float).values
    close = w["Close"].astype(float).values
    x = np.arange(len(w))

    sh = np.polyfit(x, high, 1)[0]      # 高點斜率
    sl = np.polyfit(x, low, 1)[0]       # 低點斜率
    score = 0.0
    # 1) 高低點都向下（下降）
    if sh < 0:
        score += 25
    if sl < 0:
        score += 15
    # 2) 收斂：高低點間距後半 < 前半
    rng = high - low
    first_half = rng[:len(rng) // 2].mean()
    second_half = rng[len(rng) // 2:].mean()
    if second_half < first_half:
        score += 30 * min(1.0, (first_half - second_half) / (first_half + 1e-9))
    # 3) 收斂幅度：低點斜率比高點斜率更平（楔形特徵：下緣較緩）
    if sl > sh:
        score += 15
    # 4) 最新價靠近窗內上緣（突破預備）
    pos = (close[-1] - low.min()) / (high.max() - low.min() + 1e-9)
    score += 15 * pos
    return round(float(min(100, score)), 1)


def hs_bottom_score(df):
    """
    頭肩底量化代理（0~100）：
      在窗內找 3 個谷（左肩、頭、右肩），頭最低、雙肩相近，
      且最新價已接近/突破頸線（兩峰連線）。近似，需人工確認。
    """
    if df is None or len(df) < HS_WINDOW:
        return 0.0
    w = df.tail(HS_WINDOW)
    low = w["Low"].astype(float).values
    high = w["High"].astype(float).values
    close = float(w["Close"].astype(float).values[-1])

    troughs, peaks = _local_extrema(low, order=2)
    if len(troughs) < 3:
        return 0.0
    # 取最低三谷中依時間排序，找「中間最低」的三連谷
    best = 0.0
    for i in range(len(troughs) - 2):
        a, b, c = troughs[i], troughs[i + 1], troughs[i + 2]
        ls, head, rs = low[a], low[b], low[c]
        if head < ls and head < rs:                       # 頭最低
            shoulder_sym = 1 - abs(ls - rs) / (max(ls, rs) + 1e-9)   # 雙肩對稱度
            if shoulder_sym < 0.85:                       # 雙肩差太多就跳過
                continue
            s = 0.0
            s += 35                                        # 三谷+頭最低 基本分
            s += 25 * max(0, shoulder_sym)                 # 對稱度
            # 頸線：左肩~頭之間與頭~右肩之間的高點連線，近似取窗內近高
            neckline = np.percentile(high, 70)
            if close >= neckline * 0.95:                   # 接近/突破頸線
                s += 25
            depth = (min(ls, rs) - head) / (head + 1e-9)   # 頭部深度
            s += 15 * min(1.0, depth * 10)
            best = max(best, s)
    return round(float(min(100, best)), 1)


def analyze(code, name):
    daily = fetch(code, "1d", "6mo")
    if daily.empty or len(daily) < max(WEDGE_WINDOW, 30):
        return None
    intraday = fetch(code, "60m", "60d")

    price = float(daily["Close"].iloc[-1])
    in_price = PRICE_MIN <= price <= PRICE_MAX

    macd_ok, macd_prev, macd_cur = macd_just_turned_red(daily["Close"])
    d_ok, d_detail = three_black_then_red(daily)
    h_ok, h_detail = (three_black_then_red(intraday) if not intraday.empty else (False, {}))

    wedge = falling_wedge_score(daily)
    hs = hs_bottom_score(daily)
    pattern_score = max(wedge, hs)

    # 硬條件命中數（①③④⑤）
    core = [in_price, macd_ok, d_ok, h_ok]
    core_hits = sum(core)

    return {
        "code": code, "name": name, "price": round(price, 2),
        "①價格": "✓" if in_price else "✗",
        "③MACD翻紅": "✓" if macd_ok else "✗",
        "④日線三黑轉紅": "✓" if d_ok else "✗",
        "⑤60分三黑轉紅": "✓" if h_ok else ("✗" if not intraday.empty else "—"),
        "硬條件數": core_hits,
        "型態分(楔形/頭肩底取大)": pattern_score,
        "楔形": wedge, "頭肩底": hs,
        "_macd_hist": f"{macd_prev:.3f}->{macd_cur:.3f}" if macd_prev == macd_prev else "n/a",
        "_日線量比": d_detail.get("vol_ratio_redK"),
        "_60分量比": h_detail.get("vol_ratio_redK"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-pass-only", action="store_true",
                    help="只顯示 ①③④⑤ 全部命中的標的")
    ap.add_argument("--min-core", type=int, default=2,
                    help="至少命中幾個硬條件才列出（預設2，方便看 near-miss）")
    args = ap.parse_args()

    from universe import UNIVERSE
    rows = []
    total = len(UNIVERSE)
    for i, (code, name) in enumerate(UNIVERSE.items(), 1):
        print(f"[{i}/{total}] {code} {name} ...", flush=True)
        try:
            r = analyze(code, name)
            if r:
                rows.append(r)
        except Exception as e:
            print(f"    跳過 {code}: {e}")
        time.sleep(SLEEP_SEC)

    if not rows:
        print("\n無資料。可能是速率限制或代號問題，稍後重試。")
        return

    df = pd.DataFrame(rows)
    df = df.sort_values(["硬條件數", "型態分(楔形/頭肩底取大)"], ascending=False)

    show = df.copy()
    if args.full_pass_only:
        show = show[show["硬條件數"] == 4]
    else:
        show = show[show["硬條件數"] >= args.min_core]

    cols = ["code", "name", "price", "①價格", "③MACD翻紅", "④日線三黑轉紅",
            "⑤60分三黑轉紅", "硬條件數", "型態分(楔形/頭肩底取大)", "楔形", "頭肩底"]

    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.width", 200)
    print("\n" + "=" * 90)
    print(f"掃描完成：{len(df)} 檔有資料。日期 {pd.Timestamp.now():%Y-%m-%d %H:%M}")
    print("排序：硬條件命中數 → 型態近似分。型態分僅供初篩，請人工看圖確認。")
    print("=" * 90)
    if show.empty:
        print(f"沒有標的達到門檻（min-core={args.min_core}）。下面列出全部供參考：\n")
        show = df
    print(show[cols].to_string(index=False))

    out = "screen_result.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n完整結果（含 MACD/量比明細）已存：{out}")
    print("免責：技術面資訊整理，非投資建議。型態為量化近似，務必人工確認。")


if __name__ == "__main__":
    main()
