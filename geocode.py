#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import pandas as pd
import json
import re

CSV_FOLDER = "/Users/riis/csv_output"
OUTPUT_FOLDER = "/Users/riis/store-map/data"
CACHE_FILE = "/Users/riis/store-map/geocache.json"

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

def zipcloud_lookup(postal_code, cache):
    """郵便番号→住所（都道府県+市区町村+町域）"""
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
            time.sleep(0.2)
            return address
    except:
        pass

    cache[key] = None
    return None

def geocode(query, cache):
    """国土地理院APIで住所→座標変換"""
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
            return lat, lng
    except:
        pass

    cache[query] = None
    return None, None

def resolve_address(raw_address, postal_code, cache):
    """
    住所解決の優先順位:
    1. 完全住所でジオコーディング
    2. 郵便番号→zipcloudで住所補完→ジオコーディング
    3. 郵便番号だけでジオコーディング（最終手段）
    """
    # 1. 完全住所
    cleaned = clean_address(raw_address)
    if cleaned and len(cleaned) >= 5:
        lat, lng = geocode(cleaned, cache)
        if lat:
            time.sleep(0.2)
            return lat, lng, "address"

    # 2. 郵便番号→zipcloud補完
    if postal_code:
        zip_address = zipcloud_lookup(postal_code, cache)
        if zip_address:
            # zipcloudの住所 + 元住所の残り部分を結合
            remaining = cleaned if cleaned and len(cleaned) >= 2 else ""
            full = zip_address + remaining if remaining and remaining not in zip_address else zip_address
            lat, lng = geocode(full, cache)
            if lat:
                time.sleep(0.2)
                return lat, lng, "zipcloud"

        # 3. 郵便番号をそのまま投げる
        lat, lng = geocode(postal_code, cache)
        if lat:
            time.sleep(0.2)
            return lat, lng, "postal"

    return None, None, None

def process_csv(filepath, region_name, cache):
    df = None
    for enc in ["shift-jis", "utf-8", "cp932", "utf-8-sig", "latin-1"]:
        try:
            df = pd.read_csv(filepath, encoding=enc)
            break
        except:
            continue
    if df is None:
        print(f"  ❌ 読み込みエラー: 文字コード不明")
        return None

    if "店舗名" not in df.columns:
        print(f"  ⚠️ 店舗名列なし")
        return None

    if "住所" not in df.columns:
        df["住所"] = ""
    if "ステータス" not in df.columns:
        df["ステータス"] = "RECEIPT"

    # 地域の代表座標（最終フォールバック用）
    region_lat, region_lng = geocode(region_name, cache)
    if region_lat:
        time.sleep(0.2)

    lats, lngs, methods = [], [], []
    total = len(df)
    counts = {"address": 0, "zipcloud": 0, "postal": 0, "region": 0, "fail": 0}

    for i, row in df.iterrows():
        raw_address = str(row.get("住所", "")).strip()
        postal_code = extract_postal_code(raw_address)

        lat, lng, method = resolve_address(raw_address, postal_code, cache)

        if lat is None and region_lat:
            lat, lng, method = region_lat, region_lng, "region"

        lats.append(lat)
        lngs.append(lng)
        methods.append(method or "fail")
        counts[method or "fail"] = counts.get(method or "fail", 0) + 1

        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{total}件処理中...")
            save_cache(cache)

    df["lat"] = lats
    df["lng"] = lngs
    df["region"] = region_name
    df["_method"] = methods

    df_ok = df.dropna(subset=["lat", "lng"])
    success = len(df_ok)
    print(f"  ✅ {success}/{total}件 | 住所:{counts['address']} zipcloud:{counts['zipcloud']} 郵便:{counts['postal']} 地域:{counts['region']} 失敗:{counts['fail']}")
    return df_ok

def main():
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    cache = load_cache()
    print(f"💾 キャッシュ: {len(cache)}件読み込み済み")

    csv_files = sorted([f for f in os.listdir(CSV_FOLDER) if f.endswith(".csv")])
    total_files = len(csv_files)
    print(f"📂 {total_files}個のCSVファイルを処理します")
    print("=" * 60)

    all_data = []
    total_counts = {"address": 0, "zipcloud": 0, "postal": 0, "region": 0, "fail": 0}

    for idx, filename in enumerate(csv_files):
        region_name = filename.replace(".csv", "")
        filepath = os.path.join(CSV_FOLDER, filename)
        output_path = os.path.join(OUTPUT_FOLDER, filename)

        if os.path.exists(output_path):
            print(f"✅ [{idx+1}/{total_files}] {region_name} - スキップ")
            try:
                df = pd.read_csv(output_path, encoding="utf-8")
                all_data.append(df)
                # カウント集計
                if "_method" in df.columns:
                    for m in ["address", "zipcloud", "postal", "region", "fail"]:
                        total_counts[m] += (df["_method"] == m).sum()
            except:
                pass
            continue

        print(f"🔄 [{idx+1}/{total_files}] {region_name} を処理中...")
        df = process_csv(filepath, region_name, cache)

        if df is not None and len(df) > 0:
            df.to_csv(output_path, index=False, encoding="utf-8")
            all_data.append(df)
            if "_method" in df.columns:
                for m in ["address", "zipcloud", "postal", "region", "fail"]:
                    total_counts[m] += (df["_method"] == m).sum()
        else:
            print(f"  ⚠️ データなし")

        save_cache(cache)

    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        combined = combined.dropna(subset=["lat", "lng"])

        cols = ["店舗名", "住所", "ステータス", "region", "lat", "lng"]
        cols = [c for c in cols if c in combined.columns]
        combined = combined[cols]

        json_path = "/Users/riis/store-map/stores.json"
        combined.to_json(json_path, orient="records", force_ascii=False)

        print("=" * 60)
        print(f"🎉 完了！合計 {len(combined)}件")
        print(f"  📍 住所ジオコーディング : {total_counts['address']}件")
        print(f"  📮 郵便番号→zipcloud補完: {total_counts['zipcloud']}件")
        print(f"  🔢 郵便番号直接       : {total_counts['postal']}件")
        print(f"  🗾 地域中心フォールバック: {total_counts['region']}件")
        print(f"  ❌ 座標取得失敗       : {total_counts['fail']}件")
        print(f"📄 出力: {json_path}")

if __name__ == "__main__":
    main()
