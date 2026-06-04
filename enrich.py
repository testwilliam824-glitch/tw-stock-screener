# -*- coding: utf-8 -*-
"""
決選股加料：基本面（PE/PB/殖利率）+ 籌碼（外資/投信/三大法人買賣超，張）
全部官方來源、一次抓全市場：
  上市：TWSE T86（法人）、BWIBBU_d（估值）
  上櫃：TPEx openapi 3insti_daily_trading（法人）、mainboard_peratio_analysis（估值）
買賣超單位：股 → 轉「張」(/1000)。資料為「最近一個交易日」。
"""
import datetime
import requests

H = {"User-Agent": "Mozilla/5.0"}


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").strip())
    except Exception:
        return None


def _lots(s):
    v = _num(s)
    return round(v / 1000, 1) if v is not None else None


def _twse(url, max_back=7):
    """TWSE 報表往回找最近一個有資料的交易日。"""
    d = datetime.date.today()
    for _ in range(max_back):
        try:
            r = requests.get(url, params={"response": "json",
                             "date": d.strftime("%Y%m%d"), "selectType": "ALL"},
                             headers=H, timeout=25)
            j = r.json()
            if j.get("stat") == "OK" and j.get("data"):
                return j["data"], d.strftime("%Y-%m-%d")
        except Exception:
            pass
        d -= datetime.timedelta(days=1)
    return [], None


def build_enrichment(verbose=True):
    """回傳 {股號: {三大法人張, 外資張, 投信張, PE, PB, 殖利率}}。"""
    data = {}

    def slot(c):
        return data.setdefault(c, {})

    # 上市：法人 T86
    rows, d1 = _twse("https://www.twse.com.tw/fund/T86")
    for row in rows:
        if len(row) < 19:
            continue
        s = slot(row[0].strip())
        s["外資張"] = _lots(row[4]); s["投信張"] = _lots(row[10]); s["三大法人張"] = _lots(row[18])
    # 上市：估值 BWIBBU
    rows, d2 = _twse("https://www.twse.com.tw/exchangeReport/BWIBBU_d")
    for row in rows:
        if len(row) < 7:
            continue
        s = slot(row[0].strip())
        s["殖利率"] = _num(row[3]); s["PE"] = _num(row[5]); s["PB"] = _num(row[6])
    # 上櫃：法人
    try:
        for row in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
                                headers=H, timeout=25).json():
            c = str(row.get("SecuritiesCompanyCode", "")).strip()
            s = slot(c)
            s["外資張"] = _lots(row.get("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"))
            s["投信張"] = _lots(row.get("SecuritiesInvestmentTrustCompanies-Difference"))
            s["三大法人張"] = _lots(row.get("TotalDifference"))
    except Exception:
        pass
    # 上櫃：估值
    try:
        for row in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
                                headers=H, timeout=25).json():
            c = str(row.get("SecuritiesCompanyCode", "")).strip()
            s = slot(c)
            s["PE"] = _num(row.get("PriceEarningRatio")); s["PB"] = _num(row.get("PriceBookRatio"))
            s["殖利率"] = _num(row.get("YieldRatio"))
    except Exception:
        pass

    if verbose:
        print(f"   加料資料：上市法人={d1} 估值={d2}，共 {len(data)} 檔有基本面/籌碼")
    return data


# 加進報告的欄位順序
ENRICH_COLS = ["三大法人張", "外資張", "投信張", "PE", "PB", "殖利率"]
