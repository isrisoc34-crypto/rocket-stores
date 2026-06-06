#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
店舗CSVに緯度・経度を追加するスクリプト
使い方: python3 geocode.py
"""

import os
import time
import requests
import pandas as pd
import json

CSV_FOLDER = "/Users/riis/csv_output"
OUTPUT_FOLDER = "/Users/riis/store-map/data"
CACHE_FILE = "/Users/riis/store-map/geocache.json"

# キャッシュ読み込み（一度変換した住所は再利用）
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# HeartRails Geocoder（日本の住所専用・無料）
def geocode(address, cache):
    # 郵便番号を除去
    cleaned = address.replace("〒", "").strip()
    import re
    cleaned = re.sub(r'^\d{3}-\d{4}\s*', '', cleaned).strip()

    if not cleaned:
        return None, None

    if cleaned in cache:
        return cache[cleaned]["lat"], cache[cleaned]["lng"]

    try:
        url = "https://express.heartrails.com/api/json"
        params = {"method": "getLocation", "address": cleaned}
        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if "response" in data and "location" in data["response"]:
            loc = data["response"]["location"][0]
            lat = float(loc["y"])
            lng = float(loc["x"])
            cache[cleaned] = {"lat": lat, "lng": lng}
            return lat, lng
    except Exception as e:
        pass

    return None, None

def process_csv(filepath, region_name, cache):
    try:
        df = pd.read_csv(filepath, encoding="shift-jis")
    except:
        try:
            df = pd.read_csv(filepath, encoding="utf-8")
        except Exception as e:
            print(f"  ❌ 読み込みエラー: {e}")
            return None

    if "店舗名" not in df.columns or "住所" not in df.columns:
        print(f"  ⚠️ 列名が見つかりません: {df.columns.tolist()}")
        return None

    lats, lngs = [], []
    total = len(df)

    for i, row in df.iterrows():
        address = str(row["住所"])
        lat, lng = geocode(address, cache)
        lats.append(lat)
        lngs.append(lng)

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{total}件処理中...")
            save_cache(cache)

        time.sleep(0.3)  # レート制限

    df["lat"] = lats
    df["lng"] = lngs
    df["region"] = region_name

    # 座標が取れたものだけ残す
    df = df.dropna(subset=["lat", "lng"])
    return df

def main():
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    cache = load_cache()

    csv_files = sorted([f for f in os.listdir(CSV_FOLDER) if f.endswith(".csv")])
    total_files = len(csv_files)

    print(f"📂 {total_files}個のCSVファイルを処理します")
    print(f"📍 出力先: {OUTPUT_FOLDER}")
    print("=" * 50)

    all_data = []

    for idx, filename in enumerate(csv_files):
        region_name = filename.replace(".csv", "")
        filepath = os.path.join(CSV_FOLDER, filename)
        output_path = os.path.join(OUTPUT_FOLDER, filename)

        # すでに処理済みならスキップ
        if os.path.exists(output_path):
            print(f"✅ [{idx+1}/{total_files}] {region_name} - スキップ（処理済み）")
            df = pd.read_csv(output_path, encoding="utf-8")
            all_data.append(df)
            continue

        print(f"🔄 [{idx+1}/{total_files}] {region_name} を処理中...")
        df = process_csv(filepath, region_name, cache)

        if df is not None and len(df) > 0:
            df.to_csv(output_path, index=False, encoding="utf-8")
            all_data.append(df)
            print(f"  ✅ {len(df)}件完了")
        else:
            print(f"  ⚠️ データなし")

        save_cache(cache)

    # 全データをまとめたJSONを作成（地図アプリ用）
    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        combined = combined.dropna(subset=["lat", "lng"])
        json_path = "/Users/riis/store-map/stores.json"
        combined.to_json(json_path, orient="records", force_ascii=False)
        print("=" * 50)
        print(f"🎉 完了！合計 {len(combined)}件")
        print(f"📄 地図データ: {json_path}")

if __name__ == "__main__":
    main()
