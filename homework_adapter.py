import sqlite3
import os
import re
from collections import defaultdict
from flask import Flask, request, jsonify

# 1. p.data dosyasını crawler.db'den üretme
def generate_p_data(db_path="crawler.db", out_path="data/storage/p.data"):
    # Klasörü oluştur
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    
    if not os.path.exists(db_path):
        print(f"Hata: {db_path} bulunamadı. Lütfen önce crawler'ı çalıştırıp veri toplayın.")
        return False

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT url, origin_url, depth, title, body_text FROM pages")
    except sqlite3.OperationalError:
        print("Hata: 'pages' tablosu bulunamadı. Lütfen önce arayüzden bir tarama (crawl) başlatın.")
        return False
    
    # word -> url -> {origin, depth, freq}
    word_stats = defaultdict(lambda: defaultdict(lambda: {"origin": "", "depth": 0, "freq": 0}))
    
    for url, origin_url, depth, title, body_text in cur.fetchall():
        # Başlık ve metni birleştir, küçük harfe çevir
        text = f"{title or ''} {body_text or ''}".lower()
        # Sadece alfanumerik kelimeleri al
        tokens = re.findall(r"[a-z0-9\u00c0-\u024f\u0400-\u04ff]+", text)
        
        for token in tokens:
            word_stats[token][url]["freq"] += 1
            word_stats[token][url]["origin"] = origin_url
            word_stats[token][url]["depth"] = depth
            
    # p.data dosyasına yaz (word url origin depth frequency)
    with open(out_path, "w", encoding="utf-8") as f:
        for word, urls in word_stats.items():
            for url, stats in urls.items():
                f.write(f"{word} {url} {stats['origin']} {stats['depth']} {stats['freq']}\n")
                
    print(f"Başarılı: {out_path} dosyası veritabanından üretildi!")
    return True

# 2. 3600 Portunda Çalışacak Flask API
app = Flask(__name__)

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('query', '').lower()
    results = []
    
    try:
        with open("data/storage/p.data", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                # word url origin depth frequency formatını kontrol et
                if len(parts) >= 5 and parts[0] == query:
                    url = parts[1]
                    origin = parts[2]
                    depth = int(parts[3])
                    freq = int(parts[4])
                    
                    # Hocanın istediği skor formülü: score = (frequency x 10) + 1000 - (depth x 5)
                    score = (freq * 10) + 1000 - (depth * 5)
                    
                    results.append({
                        "url": url,
                        "relevance_score": score,
                        "origin_url": origin,
                        "depth": depth,
                        "frequency": freq
                    })
    except FileNotFoundError:
        return jsonify({"error": "p.data dosyasi bulunamadi"}), 404

    # Skorlara göre büyükten küçüğe sırala
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return jsonify({"query": query, "results": results})

if __name__ == '__main__':
    # Önce dosyayı oluştur, başarılıysa sunucuyu başlat
    if generate_p_data():
        print("API 3600 portunda başlatılıyor...")
        app.run(port=3600)