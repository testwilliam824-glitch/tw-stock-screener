# -*- coding: utf-8 -*-
"""
跨平台每日執行器（取代 mac 專用 daily_scan.sh）
可在 macOS / Windows / Linux / 雲端(GitHub Actions 等) 執行：
    python run_daily.py
流程：① 檢查官方資料是否為今日 → ② 跑 screener2.py 全市場掃描
      → ③ 輸出 reports/YYYY-MM-DD.txt → ④ 盡力發系統通知(有就發，沒有就略過)
不依賴任何本機檔案以外的東西；資料全部來自公開 API。
"""
import sys, subprocess, datetime, platform, shutil
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
REPORTS.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0"}


def official_data_is_today():
    """證交所 STOCK_DAY_ALL 的資料日期是否 = 今天（避免假日/未發布時重報舊資料）。"""
    try:
        r = requests.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL",
                         params={"response": "json"}, headers=UA, timeout=20)
        d = str(r.json().get("date", ""))
        today = datetime.date.today().strftime("%Y%m%d")
        return d == today, d
    except Exception as e:
        return False, f"ERR:{e}"


def notify(title, msg):
    """盡力發系統通知，跨平台；失敗就安靜略過。"""
    try:
        sysname = platform.system()
        if sysname == "Darwin":
            subprocess.run(["osascript", "-e",
                            f'display notification "{msg}" with title "{title}"'], timeout=10)
        elif sysname == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, msg], timeout=10)
        elif sysname == "Windows":
            ps = (f'powershell -c "[reflection.assembly]::loadwithpartialname(\'System.Windows.Forms\');'
                  f'[System.Windows.Forms.MessageBox]::Show(\'{msg}\',\'{title}\')"')
            subprocess.run(ps, shell=True, timeout=10)
    except Exception:
        pass


def main():
    force = "--force" in sys.argv           # 測試用：略過新鮮度檢查
    date = datetime.date.today().strftime("%Y-%m-%d")
    fresh, dval = official_data_is_today()
    if not fresh and not force:
        (REPORTS / "skip.log").open("a", encoding="utf-8").write(
            f"[{date}] 跳過：官方資料非今日（{dval}）。\n")
        print(f"[{date}] 跳過（官方資料日={dval}）")
        return

    out = REPORTS / f"{date}.txt"
    print(f"[{date}] 開始全市場掃描 ...")
    with out.open("w", encoding="utf-8") as f:
        f.write(f"===== 台股全市場選股掃描  {date} =====\n")
        f.flush()
        # 用「同一個」python 直譯器跑 screener2.py，跨平台
        proc = subprocess.run([sys.executable, str(HERE / "screener2.py"),
                               "--macd-n", "3", "--months", "6"],
                              stdout=f, stderr=subprocess.STDOUT, cwd=str(HERE))

    # 摘要
    text = out.read_text(encoding="utf-8", errors="ignore")
    summary = " ".join(l.strip() for l in text.splitlines()
                       if ("全條件命中" in l or "日線決選（" in l))[:120]
    notify(f"台股選股 {date}", summary or "掃描完成")
    (REPORTS / "run.log").open("a", encoding="utf-8").write(f"[{date}] 完成 -> {out.name}\n")
    print(f"[{date}] 完成 -> {out}")
    print(summary)

    # 在 GitHub Actions 上自動把報告 + 儀表板資料推回 repo（本機跑則不推）
    if os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            subprocess.run(["git", "add", "docs/data", "reports/_範例報告.txt",
                            str(out.relative_to(HERE)), "reports/run.log", "reports/skip.log"],
                           cwd=str(HERE))
            # 沒有變更就跳過
            if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(HERE)).returncode != 0:
                subprocess.run(["git", "-c", "user.name=stock-scan-bot",
                                "-c", "user.email=actions@users.noreply.github.com",
                                "commit", "-m", f"儀表板更新 {date}"], cwd=str(HERE))
                subprocess.run(["git", "push"], cwd=str(HERE))
                print("已推送儀表板資料到 repo")
        except Exception as e:
            print("CI 推送失敗:", e)


if __name__ == "__main__":
    main()
