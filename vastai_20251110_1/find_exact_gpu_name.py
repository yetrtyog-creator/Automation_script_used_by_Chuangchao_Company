#!/usr/bin/env python3
"""
從 Vast.ai 獲取實際可用的 GPU 名稱列表
這會告訴我們 RTX 5090 在 API 中實際叫什麼名字
"""
import subprocess
import json
import shutil
import sys
from collections import defaultdict

def find_cli():
    for name in ["vastai", "vast"]:
        path = shutil.which(name)
        if path:
            return path
    print("❌ 找不到 vast CLI")
    sys.exit(1)

def get_all_gpu_names(cli):
    """獲取所有可用的GPU名稱"""
    print("正在獲取 Vast.ai 上所有可用的 GPU...")
    print("=" * 70)
    
    # 執行最寬鬆的搜尋
    cmd = [cli, "search", "offers", "--raw", "rentable=1"]
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            print(f"❌ 搜尋失敗: {result.stderr}")
            return []
        
        data = json.loads(result.stdout)
        offers = data.get("offers", data) if isinstance(data, dict) else data
        
        print(f"✅ 獲取了 {len(offers)} 個 offer")
        return offers
        
    except Exception as e:
        print(f"❌ 錯誤: {e}")
        return []

def analyze_gpu_names(offers):
    """分析並列出所有GPU名稱，特別關注5090"""
    gpu_count = defaultdict(int)
    rtx_5090_offers = []
    
    for offer in offers:
        gpu_name = offer.get("gpu_name", "")
        if gpu_name:
            gpu_count[gpu_name] += 1
            
            # 檢查是否與5090相關
            if "5090" in gpu_name:
                rtx_5090_offers.append(offer)
    
    return gpu_count, rtx_5090_offers

def main():
    print("🔍 Vast.ai GPU 名稱分析工具")
    print("=" * 70)
    
    cli = find_cli()
    print(f"✅ 使用 CLI: {cli}\n")
    
    offers = get_all_gpu_names(cli)
    if not offers:
        print("❌ 無法獲取 offer 列表")
        return
    
    gpu_count, rtx_5090_offers = analyze_gpu_names(offers)
    
    # 顯示所有包含 "RTX" 或 "5090" 的GPU
    print("\n" + "=" * 70)
    print("🎮 所有包含 'RTX' 或 '5090' 的 GPU 名稱：")
    print("=" * 70)
    
    rtx_gpus = {name: count for name, count in gpu_count.items() 
                if "RTX" in name.upper() or "5090" in name}
    
    if rtx_gpus:
        for gpu_name in sorted(rtx_gpus.keys()):
            count = rtx_gpus[gpu_name]
            marker = "🎯" if "5090" in gpu_name else "  "
            print(f"{marker} '{gpu_name}' - {count} 個可用")
    else:
        print("❌ 沒有找到包含 'RTX' 或 '5090' 的 GPU")
    
    # 如果找到 5090，顯示詳細信息
    if rtx_5090_offers:
        print("\n" + "=" * 70)
        print(f"🎯 找到 {len(rtx_5090_offers)} 個 RTX 5090 相關的 offer！")
        print("=" * 70)
        
        print("\n正確的 GPU 名稱格式：")
        unique_names = set(o.get("gpu_name", "") for o in rtx_5090_offers)
        for name in sorted(unique_names):
            print(f"  ✅ '{name}'")
        
        print("\n前 5 個 RTX 5090 offer 的詳細信息：")
        print("-" * 70)
        
        for i, offer in enumerate(rtx_5090_offers[:5], 1):
            gpu = offer.get("gpu_name", "N/A")
            oid = offer.get("id", "N/A")
            dph = offer.get("dph", "N/A")
            dph_total = offer.get("dph_total", "N/A")
            country = offer.get("geolocation", "N/A")
            rentable = offer.get("rentable", False)
            rented = offer.get("rented", False)
            verified = offer.get("verification", "N/A")
            
            print(f"\n[{i}] Offer ID: {oid}")
            print(f"    GPU: '{gpu}'")
            print(f"    價格: ${dph}/h (total: ${dph_total}/h)")
            print(f"    位置: {country}")
            print(f"    Rentable: {rentable}, Rented: {rented}, Verified: {verified}")
        
        # 價格統計
        prices = []
        for offer in rtx_5090_offers:
            dph = offer.get("dph")
            if dph is not None:
                try:
                    prices.append(float(dph))
                except (ValueError, TypeError):
                    pass
        
        if prices:
            print(f"\n💰 RTX 5090 價格範圍:")
            print(f"    最低: ${min(prices):.3f}/h")
            print(f"    最高: ${max(prices):.3f}/h")
            print(f"    平均: ${sum(prices)/len(prices):.3f}/h")
    else:
        print("\n" + "=" * 70)
        print("❌ 沒有找到任何包含 '5090' 的 GPU")
        print("=" * 70)
        print("\n可能的原因：")
        print("  1. 使用的 API key 可能沒有權限")
        print("  2. 網頁版和 API 可能有差異")
        print("  3. 需要特殊的查詢方式")
    
    # 給出配置建議
    if rtx_5090_offers:
        print("\n" + "=" * 70)
        print("📝 配置建議")
        print("=" * 70)
        
        unique_names = set(o.get("gpu_name", "") for o in rtx_5090_offers)
        if unique_names:
            correct_name = sorted(unique_names)[0]
            print(f"\n在 config_refactored.yaml 中使用：")
            print(f"```yaml")
            print(f"gpu_names:")
            print(f"  - {correct_name}  # ← 正確的格式")
            print(f"```")
        
        if prices:
            print(f"\n建議的價格範圍：")
            print(f"```yaml")
            print(f"price:")
            print(f"  min_dph: {max(0.1, min(prices) * 0.8):.2f}")
            print(f"  max_dph: {max(prices) * 1.2:.2f}")
            print(f"```")
    
    # 顯示完整的GPU列表統計
    print("\n" + "=" * 70)
    print(f"📊 總共找到 {len(gpu_count)} 種不同的 GPU")
    print("=" * 70)
    print("\nTop 20 GPU (按數量排序):")
    for gpu_name, count in sorted(gpu_count.items(), key=lambda x: x[1], reverse=True)[:20]:
        print(f"  {gpu_name}: {count}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  已取消")
        sys.exit(130)
