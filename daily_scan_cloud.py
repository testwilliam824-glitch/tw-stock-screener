# -*- coding: utf-8 -*-
"""
台股每日全市場技術選股（雲端獨立版，零本機相依）
條件：①價格10-100 ②頭肩底/下降楔形(量化代理) ③MACD N日內翻紅
      ④日線三黑量縮→紅K量微增 ⑤60分線同型態(只對決選股查)
資料：證交所 STOCK_DAY_ALL + 櫃買 OpenAPI(官方快照、價格漏斗) + yfinance(日線/60分,官方交叉驗證)
免責：技術面資訊整理，非投資建議。型態為量化近似，須人工看圖確認。
"""
import os, re, time, argparse, warnings
import numpy as np, pandas as pd, requests
warnings.filterwarnings("ignore")

H = {"User-Agent": "Mozilla/5.0"}
CODE_RE = re.compile(r"^[1-9]\d{3}$")
MF, MS, MG = 12, 26, 9
VOL_MICRO_MAX = 2.0
WEDGE_W, HS_W = 30, 60


def _num(s):
    try: return float(str(s).replace(",", "").replace("+", "").strip())
    except: return np.nan

def _flatten(df):
    if df is None or df.empty: return df
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy(); df.columns = df.columns.get_level_values(0)
    return df

def yf_fetch(code, interval, period):
    import yfinance as yf
    for sfx in (".TW", ".TWO"):
        try:
            df = _flatten(yf.download(code+sfx, interval=interval, period=period,
                          progress=False, auto_adjust=False, threads=False))
            if df is not None and not df.empty and len(df) > 5:
                return df.dropna(subset=["Close"])
        except: continue
    return pd.DataFrame()

def macd_hist(close):
    dif = close.ewm(span=MF, adjust=False).mean() - close.ewm(span=MS, adjust=False).mean()
    return dif - dif.ewm(span=MG, adjust=False).mean()

def macd_turned_within(close, n=3):
    h = macd_hist(close).dropna()
    if len(h) < n+2 or float(h.iloc[-1]) <= 0: return False, None
    v = h.values
    for k in range(1, n+1):
        if v[-k-1] <= 0 < v[-k]: return True, k-1
    return False, None

def three_black_then_red(df):
    if df is None or len(df) < 4: return False, {}
    o = df["Open"].astype(float).values; c = df["Close"].astype(float).values; v = df["Volume"].astype(float).values
    down = (c[-4]<o[-4]) and (c[-3]<o[-3]) and (c[-2]<o[-2]); up = c[-1] > o[-1]
    vol_shrink = v[-2] < v[-4]; ratio = (v[-1]/v[-2]) if v[-2]>0 else np.nan
    vol_micro = (v[-1] > v[-2]) and (ratio <= VOL_MICRO_MAX)
    return bool(down and up and vol_shrink and vol_micro), {"r": round(float(ratio),2) if ratio==ratio else None}

def _extrema(arr, order=2):
    t = []
    for i in range(order, len(arr)-order):
        if arr[i] == arr[i-order:i+order+1].min(): t.append(i)
    return t

def wedge_score(df):
    if df is None or len(df) < WEDGE_W: return 0.0
    w = df.tail(WEDGE_W); hi = w["High"].astype(float).values; lo = w["Low"].astype(float).values
    cl = w["Close"].astype(float).values; x = np.arange(len(w))
    sh = np.polyfit(x,hi,1)[0]; sl = np.polyfit(x,lo,1)[0]; s = 0.0
    if sh < 0: s += 25
    if sl < 0: s += 15
    rng = hi-lo; fh = rng[:len(rng)//2].mean(); sh2 = rng[len(rng)//2:].mean()
    if sh2 < fh: s += 30*min(1.0,(fh-sh2)/(fh+1e-9))
    if sl > sh: s += 15
    s += 15*((cl[-1]-lo.min())/(hi.max()-lo.min()+1e-9))
    return round(float(min(100,s)),1)

def hs_score(df):
    if df is None or len(df) < HS_W: return 0.0
    w = df.tail(HS_W); lo = w["Low"].astype(float).values; hi = w["High"].astype(float).values
    cl = float(w["Close"].astype(float).values[-1]); tr = _extrema(lo,2)
    if len(tr) < 3: return 0.0
    best = 0.0
    for i in range(len(tr)-2):
        a,b,c = tr[i],tr[i+1],tr[i+2]; ls,hd,rs = lo[a],lo[b],lo[c]
        if hd < ls and hd < rs:
            sym = 1-abs(ls-rs)/(max(ls,rs)+1e-9)
            if sym < 0.85: continue
            s = 35 + 25*max(0,sym)
            if cl >= np.percentile(hi,70)*0.95: s += 25
            s += 15*min(1.0,(min(ls,rs)-hd)/(hd+1e-9)*10)
            best = max(best,s)
    return round(float(min(100,best)),1)

def official_universe(otc=True):
    uni = {}
    try:
        r = requests.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL", params={"response":"json"}, headers=H, timeout=30)
        for row in r.json().get("data", []):
            code, name, close = row[0].strip(), row[1].strip(), _num(row[7])
            if CODE_RE.match(code) and close == close: uni[code] = (name, close, "上市")
    except Exception as e: print("上市快照失敗:", e)
    if otc:
        try:
            r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=H, timeout=30)
            for row in r.json():
                code = str(row.get("SecuritiesCompanyCode","")).strip(); name = str(row.get("CompanyName","")).strip(); close = _num(row.get("Close"))
                if CODE_RE.match(code) and close == close: uni[code] = (name, close, "上櫃")
        except Exception as e: print("上櫃快照失敗:", e)
    return uni


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price-min", type=float, default=10.0)
    ap.add_argument("--price-max", type=float, default=100.0)
    ap.add_argument("--macd-n", type=int, default=3)
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.2)
    a = ap.parse_args()

    print("① 官方全市場快照 ...", flush=True)
    uni = official_universe()
    cand = [(c,n,px,mk) for c,(n,px,mk) in uni.items() if a.price_min <= px <= a.price_max]
    cand.sort(key=lambda x: x[0])
    if a.limit: cand = cand[:a.limit]
    print(f"   全市場 {len(uni)} 檔 → 價格{a.price_min}-{a.price_max}過濾 {len(cand)} 檔", flush=True)

    rows = []
    for i,(code,name,px,mk) in enumerate(cand,1):
        if i % 50 == 0: print(f"   [{i}/{len(cand)}]", flush=True)
        try:
            d = yf_fetch(code,"1d",f"{a.months}mo")
            if d.empty or len(d) < 40: continue
            yfc = float(d["Close"].iloc[-1]); warn = abs(yfc-px)/(px+1e-9) > 0.02
            mok, dago = macd_turned_within(d["Close"], a.macd_n)
            dok, ddet = three_black_then_red(d)
            ws, hs = wedge_score(d), hs_score(d)
            rows.append({"code":code,"name":name,"market":mk,"price":round(yfc,2),
                         "macd":mok,"days_ago":dago,"daily":dok,"wedge":ws,"hs":hs,
                         "pat":max(ws,hs),"warn":warn})
        except: pass
        time.sleep(a.sleep)

    if not rows: print("無資料"); return
    df = pd.DataFrame(rows)
    fin = df[df["macd"] & df["daily"]].copy()   # ①③④ 全過（①已由漏斗保證）

    # ⑤ 60分線：只對決選股
    h60 = {}
    for code in fin["code"]:
        try:
            intr = yf_fetch(code,"60m","60d"); ok,_ = three_black_then_red(intr) if not intr.empty else (False,{})
            h60[code] = ok
        except: h60[code] = False
        time.sleep(a.sleep)
    fin["min60"] = fin["code"].map(lambda c: h60.get(c, False))

    # ===== 報告 =====
    print("\n========== 每日選股報告 ==========")
    print(f"日期 {pd.Timestamp.now():%Y-%m-%d %H:%M} | 候選 {len(df)} 檔")
    print(f"漏斗：MACD{a.macd_n}日內翻紅 {int(df['macd'].sum())} / 日線三黑轉紅 {int(df['daily'].sum())} / ①③④全過 {len(fin)} / 資料警告 {int(df['warn'].sum())}")

    full = fin[fin["min60"]]
    print(f"\n★ 全條件命中(①③④⑤)：{len(full)} 檔")
    if len(full):
        for _,r in full.sort_values("pat",ascending=False).iterrows():
            print(f"  {r['code']} {r['name']}({r['market']}) {r['price']} | 型態分{r['pat']} {'⚠資料' if r['warn'] else ''}")

    print(f"\n◆ 日線決選(①③④過，⑤待看)：{len(fin)} 檔")
    for _,r in fin.sort_values("pat",ascending=False).iterrows():
        print(f"  {r['code']} {r['name']}({r['market']}) {r['price']} | 翻紅{int(r['days_ago'])}根前 | 型態分{r['pat']}(楔{r['wedge']}/頭肩{r['hs']}) | 60分{'✓' if r['min60'] else '✗'} {'⚠' if r['warn'] else ''}")

    # near-miss
    A = df[(df["macd"])&(df["pat"]>=70)&(~df["daily"])].sort_values("pat",ascending=False).head(10)
    B = df[(df["daily"])&(df["pat"]>=70)&(~df["macd"])].sort_values("pat",ascending=False).head(10)
    print(f"\n▷ near-miss A：MACD翻紅+型態分≥70(缺日線三黑轉紅) {len(A)} 檔")
    for _,r in A.iterrows(): print(f"  {r['code']} {r['name']} {r['price']} 型態{r['pat']}")
    print(f"\n▷ near-miss B：日線三黑轉紅+型態分≥70(缺MACD翻紅) {len(B)} 檔")
    for _,r in B.iterrows(): print(f"  {r['code']} {r['name']} {r['price']} 型態{r['pat']}")
    print("\n免責：技術面資訊整理，非投資建議。型態為量化近似，務必人工看圖確認。")


if __name__ == "__main__":
    main()
