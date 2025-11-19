import requests
import json
import time
from datetime import datetime, timezone
import statistics
import os 
import telegram 
import asyncio 

try:
    import pandas as pd
    import pandas_ta as ta
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

# --- AREA PENGATURAN UTAMA ---
JUMLAH_ASET_DIPINDAI = 250
MATA_UANG = "usd"
JEDA_UPDATE_MONITOR_DETIK = 60
PERIODE_HISTORIS_HARI = 250 
JEDA_ANTAR_API_DETIK = 27 
JUMLAH_TOP_RANKING = 5
JUMLAH_KANDIDAT_AWAL = 20
MIN_SKOR_FUNDAMENTAL = 35
MIN_SKOR_NOTIFIKASI = 50 
WAKTU_COOLDOWN_JIKA_ZONK = 300
# -----------------------------

# --- PENGATURAN FILTER & SKOR ---
MOMENTUM_MIN_KENAIKAN_24JAM = 5.0 
KONSOLIDASI_MAKS_PERBAHAN_7HARI = 5.0
KONSOLIDASI_MIN_KENAIKAN_24JAM = 5.0
REBOUND_MIN_PENURUNAN_7HARI = -15.0
REBOUND_MIN_KENAIKAN_24JAM = 0.0
RSI_OVERBOUGHT_THRESHOLD = 70
# ---------------------------------

async def kirim_pesan_telegram(pesan):
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not bot_token:
        try: import config; bot_token = config.TELEGRAM_BOT_TOKEN; chat_id = config.TELEGRAM_CHAT_ID
        except ImportError: pass

    if not bot_token or not chat_id:
        print("   -> ERROR: Kunci Telegram tidak ditemukan.")
        return False
        
    bot = telegram.Bot(token=bot_token)
    try:
        await bot.send_message(chat_id=chat_id, text=pesan, parse_mode='Markdown')
        print("   -> 📢 Notifikasi terkirim ke Telegram.")
        return True
    except Exception as e:
        print(f"   -> Gagal mengirim pesan ke Telegram: {e}")
        return False

def tentukan_status_visual(skor):
    if skor >= 70: return "[HIJAU]"
    if skor >= 50: return "[KUNING]"
    return "[MERAH]"

def hitung_bollinger_bands_manual(data_harga, periode=20, multiplier=2):
    if len(data_harga) < periode: return None, None, None, None
    potongan_harga = data_harga[-periode:]
    try:
        garis_tengah = statistics.mean(potongan_harga)
        standar_deviasi = statistics.stdev(potongan_harga)
        garis_atas = garis_tengah + (standar_deviasi * multiplier)
        garis_bawah = garis_tengah - (standar_deviasi * multiplier)
        lebar_bb = ((garis_atas - garis_bawah) / garis_tengah) * 100 if garis_tengah > 0 else 0
        return garis_atas, garis_tengah, garis_bawah, lebar_bb
    except Exception:
        return None, None, None, None

def diagnosis_kondisi_pasar(df):
    harga_terkini = df['close'].iloc[-1]
    sma_50 = df['close'].rolling(window=50).mean().iloc[-1]
    sma_200 = df['close'].rolling(window=200).mean().iloc[-1]
    
    _, _, _, bbw_terkini = hitung_bollinger_bands_manual(df['close'].tolist())
    df['bbw'] = df['close'].rolling(window=20).apply(lambda x: hitung_bollinger_bands_manual(x.tolist())[3] if len(x) >= 20 else 0, raw=False)
    bbw_avg_50d = df['bbw'].rolling(window=50).mean().iloc[-1]

    if bbw_terkini is None or bbw_avg_50d is None: return "Pasar Transisi"
    is_trending = bbw_terkini > bbw_avg_50d
    is_squeeze = bbw_terkini < (bbw_avg_50d * 0.8)

    if harga_terkini > sma_200:
        if sma_50 > sma_200 and is_trending: return "Tren Bullish Terkonfirmasi"
        if harga_terkini > sma_50 and not is_trending: return "Pasar Datar Aman"
        if harga_terkini > sma_50 and is_squeeze: return "Konsolidasi (Squeeze)"
        return "Koreksi Sehat dalam Tren Bullish"
    else:
        if harga_terkini > sma_50: return "Tren Naik Muda (Waspada Beli)"
        if is_trending: return "Tren Bearish (JANGAN BELI)"
        return "Pasar Datar Berbahaya (JANGAN TRADING)"
    return "Pasar Transisi"

def jalankan_pemindai_hibrida():
    print(f"--- LANGKAH 1: Memindai {JUMLAH_ASET_DIPINDAI} aset teratas... ---")
    api_url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency={MATA_UANG}&order=market_cap_desc&per_page={JUMLAH_ASET_DIPINDAI}&page=1&price_change_percentage=24h,7d"
    try:
        response = requests.get(api_url, timeout=30); response.raise_for_status(); data_market = response.json()
        shortlist_dengan_skor = []
        for aset in data_market:
            harga_ubah_24j = aset.get('price_change_percentage_24h_in_currency'); harga_ubah_7h = aset.get('price_change_percentage_7d_in_currency')
            if harga_ubah_24j is None or harga_ubah_7h is None: continue
            skor, alasan = 0, ""
            if harga_ubah_24j > MOMENTUM_MIN_KENAIKAN_24JAM: skor, alasan = harga_ubah_24j, "Momentum Kuat"
            elif -KONSOLIDASI_MAKS_PERBAHAN_7HARI < harga_ubah_7h < KONSOLIDASI_MAKS_PERBAHAN_7HARI and harga_ubah_24j > KONSOLIDASI_MIN_KENAIKAN_24JAM: skor, alasan = 50, "Breakout"
            elif harga_ubah_7h < REBOUND_MIN_PENURUNAN_7HARI and harga_ubah_24j > REBOUND_MIN_KENAIKAN_24JAM: skor, alasan = abs(harga_ubah_7h), "Rebound"
            if skor > 0: shortlist_dengan_skor.append({'id': aset['id'], 'skor_ikan': skor, 'alasan': alasan, 'nama_aset': aset.get('name', aset['id'].capitalize())})

        kandidat_awal = sorted(shortlist_dengan_skor, key=lambda x: x['skor_ikan'], reverse=True)[:JUMLAH_KANDIDAT_AWAL]
        print(f"--- Filter Cepat Selesai. Ditemukan {len(kandidat_awal)} kandidat potensial. ---")
        
        if not kandidat_awal: return []

        print("--- LANGKAH 1.1: Menjalankan Filter RSI... ---")
        kandidat_final = []
        for kandidat in kandidat_awal:
            try:
                hist_api_url = f"https://api.coingecko.com/api/v3/coins/{kandidat['id']}/market_chart?vs_currency={MATA_UANG}&days=15"
                response = requests.get(hist_api_url, timeout=20); response.raise_for_status()
                df = pd.DataFrame(response.json()['prices'], columns=['timestamp', 'close'])
                if len(df) > 14:
                    rsi = ta.rsi(df['close'], length=14).iloc[-1]
                    if rsi < RSI_OVERBOUGHT_THRESHOLD:
                        kandidat['skor_gabungan'] = (kandidat['skor_ikan'] * 0.5) + (max(0, 100 - rsi) * 0.5)
                        kandidat_final.append(kandidat)
                time.sleep(JEDA_ANTAR_API_DETIK)
            except Exception as e:
                print(f"    > {kandidat['id']}: Gagal Cek RSI. Skip.")
        return sorted(kandidat_final, key=lambda x: x['skor_gabungan'], reverse=True)
    except Exception as e: 
        print(f"Terjadi error saat pemindaian awal: {e}")
        return []

def analisis_pattern(prices):
    if not prices or len(prices) < 20: return "Data Kurang", 50, None
    try:
        daily_changes = [(prices[i] - prices[i-1]) / prices[i-1] * 100 for i in range(1, len(prices))]
        volatility = statistics.stdev(daily_changes)
        if volatility < 3.5: return "Hijau (Pola Stabil)", 80, statistics.mean(abs(c) for c in daily_changes)
        if volatility > 8.5: return "Merah (Pola Acak)", 20, statistics.mean(abs(c) for c in daily_changes)
        return "Kuning (Pola Moderat)", 50, statistics.mean(abs(c) for c in daily_changes)
    except Exception: return "Gagal Analisis", 30, None

def jalankan_analisis_mendalam(daftar_pantau_terurut):
    print("--- LANGKAH 1.5: Memulai Analisis Mendalam... ---")
    data_historis_mentah, data_fundamental_mentah = {}, {}

    print("\n--- FASE 1: Mengambil Data (Historis & Fundamental)... ---")
    for aset in daftar_pantau_terurut:
        try:
            # Historis
            hist_api_url = f"https://api.coingecko.com/api/v3/coins/{aset['id']}/market_chart?vs_currency={MATA_UANG}&days={PERIODE_HISTORIS_HARI}"
            res_hist = requests.get(hist_api_url, timeout=20); res_hist.raise_for_status()
            data_historis_mentah[aset['id']] = res_hist.json()
            time.sleep(JEDA_ANTAR_API_DETIK)
            # Fundamental
            coin_api_url = f"https://api.coingecko.com/api/v3/coins/{aset['id']}?localization=false&tickers=false&market_data=false&community_data=true&developer_data=true"
            res_coin = requests.get(coin_api_url, timeout=20); res_coin.raise_for_status()
            data_fundamental_mentah[aset['id']] = res_coin.json()
            time.sleep(JEDA_ANTAR_API_DETIK)
        except Exception as e: print(f"    - Gagal mengambil data untuk {aset['id']}: {e}")

    print("\n--- FASE 2: Mengolah Data... ---")
    analisis_lengkap = {}
    for aset in daftar_pantau_terurut:
        aset_id = aset['id']
        if not (aset_id in data_historis_mentah and aset_id in data_fundamental_mentah): continue
        
        hist_data = data_historis_mentah[aset_id]
        df = pd.DataFrame(hist_data['prices'], columns=['timestamp', 'close'])
        df_vol = pd.DataFrame(hist_data['total_volumes'], columns=['timestamp', 'volume'])
        df = pd.merge(df, df_vol, on='timestamp', how='left')
        df['close'] = pd.to_numeric(df['close'], errors='coerce'); df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
        df.dropna(subset=['close', 'volume'], inplace=True); df = df[df['close'] > 0]; df.reset_index(drop=True, inplace=True)

        if len(df) < 200:
            print(f"    - {aset_id}: Data historis kurang ({len(df)}/200)."); continue

        coin_data = data_fundamental_mentah[aset_id]
        dev_score, dev_status = 30, "Gagal Cek"
        if any(coin_data.get("developer_data", {}).get("last_4_weeks_commit_activity_series", [])): dev_score, dev_status = 90, "Aktif"
        community_score, community_status = 0, "Data Tdk Tersedia"
        if followers := coin_data.get("community_data", {}).get("twitter_followers"):
            if followers > 500000: community_score, community_status = 90, f"Sangat Besar"
            elif followers > 100000: community_score, community_status = 70, f"Besar"
            else: community_score, community_status = 40, "Kecil"
        _, pattern_score, _ = analisis_pattern(df['close'].tolist())

        bobot = {'dev': 40, 'pattern': 30, 'comm': 30}
        skor_fundamental = int((dev_score * bobot['dev'] + pattern_score * bobot['pattern'] + community_score * bobot['comm']) / 100)
        
        if skor_fundamental < MIN_SKOR_FUNDAMENTAL:
            print(f"    - {aset_id}: Skor Fundamental rendah ({skor_fundamental}%). Skip."); continue

        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        
        aset_analisis = {'nama_aset': aset['nama_aset'], 'skor_fundamental': skor_fundamental}
        aset_analisis['diagnosis_pasar'] = diagnosis_kondisi_pasar(df)
        
        prices = df['close'].tolist()
        aset_analisis["support"], aset_analisis["resistance"] = min(prices), max(prices)
        aset_analisis["rsi"] = ta.rsi(df['close'], length=14).iloc[-1]
        aset_analisis["volume_ma"] = df['volume'].rolling(window=20).mean().iloc[-1]
        bb_atas, bb_tengah, bb_bawah, _ = hitung_bollinger_bands_manual(prices)
        aset_analisis["bb_upper"], aset_analisis["bb_middle"], aset_analisis["bb_lower"] = bb_atas, bb_tengah, bb_bawah
        
        macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
        aset_analisis["macd_value"] = macd_df['MACD_12_26_9'].iloc[-1]
        aset_analisis["macd_signal"] = macd_df['MACDs_12_26_9'].iloc[-1]
        aset_analisis["dev_status"], aset_analisis["community_status"] = dev_status, community_status
        aset_analisis["pattern_status"], _, _ = analisis_pattern(prices)

        analisis_lengkap[aset_id] = aset_analisis
        print(f"    - {aset_id}: Lolos Analisis Mendalam.")

    return analisis_lengkap

def ambil_berita(nama_aset):
    news_key = os.environ.get('NEWS_API_KEY')
    if not news_key:
        try: import config; news_key = config.NEWS_API_KEY
        except ImportError: return "API Key Berita tidak ditemukan."
        
    try:
        url = f"https://newsapi.org/v2/everything?q={nama_aset}&searchIn=title&domains=bloomberg.com,reuters.com,coindesk.com,cointelegraph.com&sortBy=publishedAt&pageSize=1&apiKey={news_key}"
        res = requests.get(url, timeout=10); res.raise_for_status()
        return res.json()["articles"][0]["title"] if res.json().get("articles") else "Tidak ada berita relevan."
    except Exception: return "Gagal ambil berita."

def buat_ringkasan_intelijen(skor_total, skor_teknikal, alasan_teknikal, analisis_aset):
    status = tentukan_status_visual(skor_total)
    alasan = f"Sinyal beli terdeteksi ({alasan_teknikal}), "
    fund = analisis_aset.get("skor_fundamental", 0)
    if fund >= 70: alasan += "fundamental sehat."
    elif fund >= 50: alasan += "fundamental cukup."
    else: alasan += "fundamental lemah."
    return f"{status} {alasan}"

async def jalankan_monitor(daftar_pantau_terurut, analisis_lengkap, berita_terkini):
    if not analisis_lengkap: return
    
    ids = list(analisis_lengkap.keys())
    id_str = ",".join(ids)
    print("--- Mode Monitor Aktif ---")

    while True:
        try:
            url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency={MATA_UANG}&ids={id_str}&price_change_percentage=24h"
            res = requests.get(url, timeout=20); res.raise_for_status(); data = res.json()
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Cek Harga {len(data)} aset...")
            
            for asset in data:
                id = asset['id']
                analisis = analisis_lengkap.get(id)
                if not analisis: continue

                harga = asset.get('current_price'); vol = asset.get('total_volume')
                if harga is None or vol is None: continue
                
                diagnosis = analisis['diagnosis_pasar']
                skor_tek, alasan_tek, rincian = 0, f"Wait: {diagnosis}", {}

                entry_price_target = 0
                
                if diagnosis in ["Tren Bullish Terkonfirmasi", "Koreksi Sehat dalam Tren Bullish", "Tren Naik Muda (Waspada Beli)"]:
                    entry_price_target = analisis['bb_middle'] 
                    if harga <= (entry_price_target * 1.01): 
                        rincian['Posisi'] = 40; alasan_tek = "Harga di Zona Beli (Middle Band)"
                    if 40 <= analisis['rsi'] < 60: rincian['RSI'] = 30
                    if analisis['macd_value'] > analisis['macd_signal']: rincian['MACD'] = 20
                    if vol > analisis['volume_ma']: rincian['Vol'] = 10
                    skor_tek = sum(rincian.values())

                elif diagnosis == "Pasar Datar Aman":
                    entry_price_target = analisis['bb_lower']
                    if harga <= (entry_price_target * 1.02):
                         rincian['Posisi'] = 40; alasan_tek = "Harga di Zona Beli (Lower Band)"
                    if analisis['rsi'] < 40: rincian['RSI'] = 30
                    if vol > (2 * analisis['volume_ma']): rincian['Vol'] = 15
                    if analisis.get('support', 0) > 0 and ((harga - analisis['support']) / analisis['support'] * 100) < 5: rincian['Sup'] = 15
                    skor_tek = sum(rincian.values())

                skor_tek = min(100, max(0, skor_tek))
                total_skor = int((analisis['skor_fundamental'] * 0.4) + (skor_tek * 0.6))

                status_eksekusi = "🟡 WAITING"
                if entry_price_target > 0 and harga <= (entry_price_target * 1.015):
                    status_eksekusi = "🟢 EXECUTE NOW"

                print(f"  -> {id}: Skor {total_skor}% | Status: {status_eksekusi}")

                if total_skor >= MIN_SKOR_NOTIFIKASI and status_eksekusi == "🟢 EXECUTE NOW":
                    print(f"     >>> MENGIRIM SINYAL EKSEKUSI UNTUK {id.upper()}! <<<")
                    pesan = f"🚨 *SINYAL EKSEKUSI: {analisis['nama_aset']} ({id.upper()})*\n"
                    pesan += f"`DIAGNOSIS: {diagnosis}`\n"
                    pesan += "=========================\n"
                    pesan += f"*STATUS: {status_eksekusi}*\n"
                    pesan += f"Harga: ${harga:,.4f} | Target: ~${entry_price_target:,.4f}\n\n"
                    pesan += f"*ANALISIS:*\n"
                    pesan += f"• Skor Total: {total_skor}%\n"
                    pesan += f"• Fundamental: {analisis['skor_fundamental']}%\n"
                    pesan += f"• Teknikal: {skor_tek}% (Rincian: {', '.join([f'{k}+{v}' for k,v in rincian.items()])})\n"
                    pesan += f"• RSI: {analisis['rsi']:.2f}\n"
                    pesan += f"• Vol: {'Tinggi' if vol > analisis['volume_ma'] else 'Normal'}\n\n"
                    pesan += f"*RENCANA:*\n"
                    pesan += f"• Stop Loss (Trailing): < ${analisis['support'] * 0.95:,.2f}\n"
                    pesan += f"• Exit: Ikuti Tren / BB Upper\n"
                    await kirim_pesan_telegram(pesan)
                    time.sleep(1)

            if "GITHUB_ACTIONS" in os.environ: break 
            time.sleep(JEDA_UPDATE_MONITOR_DETIK)

        except Exception as e:
            print(f"Error monitor: {e}"); time.sleep(20)
            if "GITHUB_ACTIONS" in os.environ: break
            continue

async def main():
    # === PESAN START DIJALANKAN DI SINI, DI LUAR LOGIKA UTAMA ===
    await kirim_pesan_telegram(f"✅ *Mesin Intelijen v33 HIDUP*\nSiap memindai {JUMLAH_ASET_DIPINDAI} aset...")
    # =========================================================

    if not PANDAS_AVAILABLE: print("Error: Install pandas-ta")
    else:
        while True: 
            watchlist = jalankan_pemindai_hibrida()
            if watchlist:
                top = watchlist[:JUMLAH_TOP_RANKING]
                print(f"\n--- Aset Terpilih: {[a['id'] for a in top]} ---")
                analisis = jalankan_analisis_mendalam(top)
                
                if analisis:
                    berita = {}
                    for id, data in analisis.items():
                        berita[id] = ambil_berita(data['nama_aset']); time.sleep(1)
                    await jalankan_monitor(top, analisis, berita)
                    if "GITHUB_ACTIONS" in os.environ: break
                else:
                    print("--- Semua kandidat gagal di Analisis Mendalam. ---")
            else:
                print("--- Tidak ada aset potensial di tahap Scan Awal. ---")
            
            if "GITHUB_ACTIONS" in os.environ: break
            print(f"\nMenunggu {WAKTU_COOLDOWN_JIKA_ZONK} detik sebelum scan ulang...")
            time.sleep(WAKTU_COOLDOWN_JIKA_ZONK)

if __name__ == "__main__":
    asyncio.run(main())