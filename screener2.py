# -*- coding: utf-8 -*-
"""
全市場技術選股器 v2（台股，上市+上櫃）
=================================================
資料策略（A+C）：
  • 全市場代號 + 最新收盤：證交所 STOCK_DAY_ALL（上市）+ 櫃買 OpenAPI（上櫃）— 官方、乾淨
  • 漏斗：先用「官方最新收盤」做 ①價格 10~100 過濾，砍掉一大半才抓歷史
  • 日線歷史：yfinance（快），但每檔以「官方收盤」交叉驗證，誤差>2% 標警告
  • 60分線⑤：只對「日線已通過」的少數決選股抓（把弱資料源用量降到最低）

條件：
  ① 價格 10~100        （官方快照過濾）
  ③ MACD 柱狀圖 N 日內翻紅（放寬版：最近 N 根內出現 <=0→>0 且現仍為正）
  ④ 日線 三黑量縮→紅K量微增
  ⑤ 60分線 三黑量縮→紅K量微增（只對日線決選股檢查）
  ② 頭肩底/下降楔形：量化代理分數（人工確認）

免責：技術面資訊整理，非投資建議。型態為量化近似。
"""

import os, re, time, argparse, warnings
import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")
# 重用 v1 的指標函式
from screener import (macd_hist, three_black_then_red,
                      falling_wedge_score, hs_bottom_score, fetch as yf_fetch)
from enrich import build_enrichment, ENRICH_COLS

H = {"User-Agent": "Mozilla/5.0"}
CODE_RE = re.compile(r"^[1-9]\d{3}$")   # 只要 4 位數普通股，排除 ETF(00xxx)/權證


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").strip())
    except Exception:
        return np.nan


def _get_json(url, params=None, tries=4, sleep_s=3):
    """帶重試的 GET → json；暫時性網路錯誤自動重試，全失敗才丟出。"""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=H, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(sleep_s * (i + 1))   # 漸進式退避
    raise last


def official_universe(include_otc=True):
    """官方全市場最新收盤：{code: (name, close, market)}。"""
    uni = {}
    # 上市（重試）
    try:
        j = _get_json("https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL",
                      params={"response": "json"})
        for row in j.get("data", []):
            code, name, close = row[0].strip(), row[1].strip(), _num(row[7])
            if CODE_RE.match(code) and close == close:
                uni[code] = (name, close, "上市")
    except Exception as e:
        print("⚠️ 上市快照重試後仍失敗:", e)
    # 上櫃（重試）
    if include_otc:
        try:
            j = _get_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
            for row in j:
                code = str(row.get("SecuritiesCompanyCode", "")).strip()
                name = str(row.get("CompanyName", "")).strip()
                close = _num(row.get("Close"))
                if CODE_RE.match(code) and close == close:
                    uni[code] = (name, close, "上櫃")
        except Exception as e:
            print("⚠️ 上櫃快照重試後仍失敗（本次將只掃上市，請留意覆蓋率）:", e)
    n_listed = sum(1 for v in uni.values() if v[2] == "上市")
    n_otc = sum(1 for v in uni.values() if v[2] == "上櫃")
    print(f"   覆蓋率：上市 {n_listed} + 上櫃 {n_otc} = {len(uni)} 檔")
    return uni


def macd_turned_within(close, n=3):
    """MACD 柱狀圖在最近 n 根內『翻紅』(<=0→>0) 且目前仍為正。回傳(bool, 幾根前翻)。"""
    h = macd_hist(close).dropna()
    if len(h) < n + 2:
        return False, None
    if float(h.iloc[-1]) <= 0:                 # 現在必須仍為正
        return False, None
    vals = h.values
    for k in range(1, n + 1):                  # 往回看 n 根
        if vals[-k - 1] <= 0 < vals[-k]:
            return True, k - 1                  # 0=今天剛翻
    return False, None


def analyze(code, name, official_close, market, macd_n, months):
    period = f"{months}mo"
    daily = yf_fetch(code, "1d", period)
    if daily.empty or len(daily) < 40:
        return None

    yf_close = float(daily["Close"].iloc[-1])
    # 交叉驗證：官方 vs yfinance 收盤
    diff = abs(yf_close - official_close) / (official_close + 1e-9)
    data_warn = diff > 0.02

    macd_ok, days_ago = macd_turned_within(daily["Close"], macd_n)
    d_ok, d_detail = three_black_then_red(daily)
    wedge = falling_wedge_score(daily)
    hs = hs_bottom_score(daily)

    return {
        "code": code, "name": name, "market": market,
        "price": round(yf_close, 2),
        "③MACD_Nin": macd_ok, "_翻紅幾根前": days_ago,
        "④日線三黑轉紅": d_ok,
        "_日線量比": d_detail.get("vol_ratio_redK"),
        "楔形": wedge, "頭肩底": hs, "型態分": max(wedge, hs),
        "_資料警告": data_warn,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price-min", type=float, default=10.0)
    ap.add_argument("--price-max", type=float, default=100.0)
    ap.add_argument("--macd-n", type=int, default=3, help="MACD 幾日內翻紅(放寬)")
    ap.add_argument("--months", type=int, default=6, help="日線歷史月數")
    ap.add_argument("--no-otc", action="store_true", help="只掃上市")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 檔候選(測試用)")
    ap.add_argument("--sleep", type=float, default=0.35)
    args = ap.parse_args()

    print("① 抓官方全市場快照 ...", flush=True)
    uni = official_universe(include_otc=not args.no_otc)
    print(f"   全市場普通股：{len(uni)} 檔")

    # 價格漏斗
    cand = [(c, n, px, mk) for c, (n, px, mk) in uni.items()
            if args.price_min <= px <= args.price_max]
    cand.sort(key=lambda x: x[0])
    if args.limit:
        cand = cand[:args.limit]
    print(f"② 價格 {args.price_min}-{args.price_max} 過濾後：{len(cand)} 檔，開始抓日線 ...", flush=True)

    rows = []
    for i, (code, name, px, mk) in enumerate(cand, 1):
        if i % 25 == 0 or i == len(cand):
            print(f"   [{i}/{len(cand)}] ...", flush=True)
        try:
            r = analyze(code, name, px, mk, args.macd_n, args.months)
            if r:
                rows.append(r)
        except Exception:
            pass
        time.sleep(args.sleep)

    if not rows:
        print("無資料。"); return
    df = pd.DataFrame(rows)

    # 日線決選：③MACD N日內翻紅 + ④日線三黑轉紅（價格已在漏斗保證）
    df["日線通過"] = df["③MACD_Nin"] & df["④日線三黑轉紅"]
    finalists = df[df["日線通過"]].copy()

    print(f"\n③ 日線決選（MACD {args.macd_n}日內翻紅 且 日線三黑轉紅）：{len(finalists)} 檔")
    print("④ 對決選股抓 60分線確認 ⑤ ...", flush=True)
    h_flags = {}
    for code in finalists["code"]:
        try:
            intr = yf_fetch(code, "60m", "60d")
            ok, det = three_black_then_red(intr) if not intr.empty else (False, {})
            h_flags[code] = (ok, det.get("vol_ratio_redK"))
        except Exception:
            h_flags[code] = (False, None)
        time.sleep(args.sleep)
    finalists["⑤60分三黑轉紅"] = finalists["code"].map(lambda c: h_flags.get(c, (False, None))[0])
    finalists["_60分量比"] = finalists["code"].map(lambda c: h_flags.get(c, (False, None))[1])
    finalists["全條件"] = finalists["⑤60分三黑轉紅"]   # 已含①③④，再加⑤

    # ===== 加料：基本面 + 籌碼（決選股與觀察清單都需要，故總是抓）=====
    print("⑤ 補上基本面/籌碼欄位（官方一次抓全市場）...", flush=True)
    enr = build_enrichment()
    if len(finalists):
        for col in ENRICH_COLS:
            finalists[col] = finalists["code"].map(lambda c, k=col: (enr.get(c) or {}).get(k))

    # 輸出
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.width", 220)
    df.to_csv("scan_all.csv", index=False, encoding="utf-8-sig")
    finalists.sort_values(["全條件", "型態分"], ascending=False).to_csv(
        "scan_finalists.csv", index=False, encoding="utf-8-sig")

    # ===== 輸出網頁儀表板 JSON（GitHub Pages 用）=====
    try:
        from dashboard import build_payload, write_payload
        cov = {"listed": sum(1 for v in uni.values() if v[2] == "上市"),
               "otc": sum(1 for v in uni.values() if v[2] == "上櫃")}
        date = pd.Timestamp.now().strftime("%Y-%m-%d")
        write_payload(build_payload(df, finalists, enr, cov, date))
    except Exception as e:
        print("⚠️ 儀表板 JSON 輸出失敗:", e)

    print("\n" + "=" * 80)
    print(f"全市場掃描完成 {pd.Timestamp.now():%Y-%m-%d %H:%M}")
    print("=" * 80)
    full = finalists[finalists["全條件"]]
    cols = ["code", "name", "market", "price", "③MACD_Nin", "④日線三黑轉紅",
            "⑤60分三黑轉紅", "三大法人張", "外資張", "投信張", "PE", "PB", "殖利率",
            "型態分", "_資料警告"]
    cols = [c for c in cols if c in finalists.columns]   # 決選0檔時 enrich 欄不存在，過濾掉
    if len(full):
        print(f"\n★ 全條件命中（①③④⑤全過）：{len(full)} 檔")
        print(full.sort_values("型態分", ascending=False)[cols].to_string(index=False))
    else:
        print("\n★ 全條件命中：0 檔")
    print(f"\n日線決選（①③④過，⑤待看）共 {len(finalists)} 檔，依型態分排序：")
    if len(finalists):
        print(finalists.sort_values("型態分", ascending=False)[cols].head(25).to_string(index=False))
    print("\n檔案：scan_all.csv（全部）、scan_finalists.csv（決選）")
    print("免責：技術面資訊整理，非投資建議。型態為量化近似，務必人工看圖確認。")


if __name__ == "__main__":
    main()
