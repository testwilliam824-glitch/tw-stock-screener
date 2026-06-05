# -*- coding: utf-8 -*-
"""
把掃描結果輸出成網頁儀表板用的 JSON（docs/data/）。
GitHub Pages 的 index.html 會讀 latest.json 渲染漂亮畫面。
"""
import os, json, datetime
import pandas as pd


def _row(r):
    def g(k, d=None):
        v = r.get(k, d)
        if isinstance(v, float) and pd.isna(v):
            return None
        return v
    return {
        "code": str(g("code")), "name": g("name"), "market": g("market"),
        "price": g("price"),
        "macd": bool(g("③MACD_Nin", False)),
        "macd_days": g("_翻紅幾根前"),
        "candle": bool(g("④日線三黑轉紅", False)),
        "min60": (None if g("⑤60分三黑轉紅") is None else bool(g("⑤60分三黑轉紅"))),
        "min30": (None if g("⑤30分三黑轉紅") is None else bool(g("⑤30分三黑轉紅"))),
        "pattern": g("型態分"), "wedge": g("楔形"), "hs": g("頭肩底"),
        "insti3": g("三大法人張"), "foreign": g("外資張"), "trust": g("投信張"),
        "pe": g("PE"), "pb": g("PB"), "yield": g("殖利率"),
        "warn": bool(g("_資料警告", False)),
    }


def build_payload(df_all, finalists, enrich_map, coverage, date):
    a = df_all
    funnel = {
        "candidates": int(len(a)),
        "macd": int(a["③MACD_Nin"].sum()),
        "candle": int(a["④日線三黑轉紅"].sum()),
        "finalists": int((a["③MACD_Nin"] & a["④日線三黑轉紅"]).sum()),
    }
    # 觀察清單（near-miss）：③已翻紅，依型態分排序，補上加料
    watch = a[a["③MACD_Nin"]].sort_values("型態分", ascending=False).head(12).copy()
    for col in ["三大法人張", "外資張", "投信張", "PE", "PB", "殖利率"]:
        if col not in watch.columns:
            watch[col] = watch["code"].astype(str).map(
                lambda c, k=col: (enrich_map.get(c) or {}).get(k))

    fin_list = [_row(r) for _, r in finalists.iterrows()] if len(finalists) else []
    return {
        "date": date,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "coverage": coverage,
        "funnel": funnel,
        "finalists": fin_list,
        "watch": [_row(r) for _, r in watch.iterrows()],
    }


def write_payload(payload, base="docs/data"):
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    dated = os.path.join(base, f"{payload['date']}.json")
    with open(dated, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    # 歷史索引
    idx_path = os.path.join(base, "index.json")
    dates = []
    if os.path.exists(idx_path):
        try:
            dates = json.load(open(idx_path, encoding="utf-8")).get("dates", [])
        except Exception:
            dates = []
    if payload["date"] not in dates:
        dates.append(payload["date"])
    dates = sorted(set(dates), reverse=True)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump({"dates": dates}, f, ensure_ascii=False)
    print(f"   儀表板 JSON 已輸出：{base}/latest.json（{len(payload['watch'])} 檔觀察、{len(payload['finalists'])} 檔決選）")
