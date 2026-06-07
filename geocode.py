#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excelから直接ジオコーディングして stores.json を生成するスクリプト
- 入力: /Users/riis/Desktop/data.xlsx（全76,000件超）
- 国土地理院API + zipcloud（郵便番号補完）
- キャッシュ活用で高速化・途中再開対応
"""

import os
import time
import requests
import pandas as pd
import json
import re

EXCEL_FILE   = "/Users/riis/Downloads/data (1).xlsx"
CACHE_FILE   = "/Users/riis/store-map/geocache.json"
OUTPUT_JSON  = "/Users/riis/store-map/stores.json"
PROGRESS_FILE = "/Users/riis/store-map/progress.json"  # 途中再開用

# ── キャッシュ ──────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except:
            pass
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# ── 住所クリーニング ────────────────────────────────────
def extract_postal_code(address):
    address = str(address).replace("₸", "〒").strip()
    m = re.search(r'(\d{3})-?(\d{4})', address)
    if m:
        return m.group(1) + "-" + m.group(2)
    return None

def clean_address(address):
    address = str(address).replace("₸", "〒").strip()
    address = re.sub(r'[〒₸]', '', address)
    address = re.sub(r'^\d{3}-\d{4}\s*', '', address).strip()
    address = re.sub(r'\s+\S+棟.*$', '', address)
    return address

# ── zipcloud（郵便番号→住所補完）──────────────────────
def zipcloud_lookup(postal_code, cache):
    key = f"zip:{postal_code}"
    if key in cache:
        return cache[key]
    code = postal_code.replace("-", "")
    try:
        res = requests.get(
            "https://zipcloud.ibsnet.co.jp/api/search",
            params={"zipcode": code},
            timeout=5
        )
        data = res.json()
        if data.get("results"):
            r = data["results"][0]
            address = r["address1"] + r["address2"] + r["address3"]
            cache[key] = address
            time.sleep(0.15)
            return address
    except:
        pass
    cache[key] = None
    return None

# ── 国土地理院ジオコーディング ──────────────────────────
def geocode(query, cache):
    query = str(query).strip()
    if not query or query == "nan" or len(query) < 3:
        return None, None
    if query in cache:
        entry = cache[query]
        if entry is None:
            return None, None
        return entry["lat"], entry["lng"]
    try:
        res = requests.get(
            "https://msearch.gsi.go.jp/address-search/AddressSearch",
            params={"q": query},
            timeout=5
        )
        data = res.json()
        if data and len(data) > 0:
            coords = data[0]["geometry"]["coordinates"]
            lat = float(coords[1])
            lng = float(coords[0])
            cache[query] = {"lat": lat, "lng": lng}
            time.sleep(0.15)
            return lat, lng
    except:
        pass
    cache[query] = None
    return None, None

# ── 住所解決（優先順位付き）────────────────────────────
def resolve_address(raw_address, region, cache):
    """
    1. 完全住所でジオコーディング
    2. 郵便番号→zipcloud補完→ジオコーディング
    3. 地域名でジオコーディング（最終フォールバック）
    """
    postal_code = extract_postal_code(raw_address)
    cleaned = clean_address(raw_address)

    # 1. 完全住所
    if cleaned and len(cleaned) >= 5:
        lat, lng = geocode(cleaned, cache)
        if lat:
            return lat, lng, "address"

    # 2. zipcloud補完
    if postal_code:
        zip_address = zipcloud_lookup(postal_code, cache)
        if zip_address:
            remaining = cleaned if (cleaned and len(cleaned) >= 2 and cleaned not in zip_address) else ""
            full = zip_address + remaining if remaining else zip_address
            lat, lng = geocode(full, cache)
            if lat:
                return lat, lng, "zipcloud"

    # 3. 地域フォールバック
    if region:
        lat, lng = geocode(region, cache)
        if lat:
            return lat, lng, "region"

    return None, None, "fail"

# ── メイン ─────────────────────────────────────────────
def main():
    print("📖 Excelを読み込み中...")
    df = pd.read_excel(EXCEL_FILE, header=2)
    df.columns = ["StoreID", "店舗名", "region", "住所", "住所EN", "ステータス"]
    df = df[df["ステータス"].isin(["RECEIPT", "APP_DISPLAY", "PENDING"])]
    df["住所"] = df["住所"].fillna("").astype(str)
    df = df.reset_index(drop=True)
    total = len(df)
    print(f"✅ {total:,}件のデータを読み込みました")

    cache = load_cache()
    print(f"💾 キャッシュ: {len(cache):,}件")

    # 途中再開: 処理済みインデックスを読み込む
    done_ids = set()
    results = []
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                results = saved.get("results", [])
                done_ids = set(saved.get("done_ids", []))
                print(f"🔄 途中再開: {len(done_ids):,}件処理済み")
        except:
            pass

    print("=" * 60)
    counts = {"address": 0, "zipcloud": 0, "region": 0, "fail": 0}

    for idx, row in df.iterrows():
        store_id = str(row["StoreID"])
        if store_id in done_ids:
            continue

        lat, lng, method = resolve_address(row["住所"], row["region"], cache)

        if lat:
            results.append({
                "店舗名": row["店舗名"],
                "住所": row["住所"],
                "ステータス": row["ステータス"],
                "region": row["region"],
                "lat": lat,
                "lng": lng,
            })
        counts[method] = counts.get(method, 0) + 1
        done_ids.add(store_id)

        # 500件ごとに保存・進捗表示
        if (idx + 1) % 500 == 0:
            save_cache(cache)
            # 進捗保存
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump({"results": results, "done_ids": list(done_ids)}, f, ensure_ascii=False)
            pct = len(done_ids) / total * 100
            print(f"  [{len(done_ids):,}/{total:,}] {pct:.1f}% | "
                  f"住所:{counts['address']} zipcloud:{counts['zipcloud']} "
                  f"地域:{counts['region']} 失敗:{counts['fail']}")

    # 最終保存
    save_cache(cache)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)

    # progressファイル削除（完了）
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

    print("=" * 60)
    print(f"🎉 完了！")
    print(f"  📍 住所ジオコーディング    : {counts['address']:,}件")
    print(f"  📮 郵便番号→zipcloud補完  : {counts['zipcloud']:,}件")
    print(f"  🗾 地域フォールバック      : {counts['region']:,}件")
    print(f"  ❌ 取得失敗               : {counts['fail']:,}件")
    print(f"  📦 合計出力               : {len(results):,}件 / {total:,}件")
    print(f"  📄 出力先: {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
