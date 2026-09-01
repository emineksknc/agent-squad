"""
Ortak LLM çağrı yardımcı fonksiyonu — local modellerin (qwen2.5 vb.) bazen JSON
formatını bozarak/bozularak (dil karışması, yarım cümle, kırık syntax) cevap
verebildiğini gördük (bkz. gerçek çalıştırma logu — bir çağrıda model aniden
Çince'ye kayıp JSON'u kırdı). Bu fonksiyon her agent çağrısını retry ile sarar.

Kullanım: her agent, ham `client.chat(...)` çağrısı yerine bunu kullanmalı.
"""
import json
import ollama

MAX_RETRIES = 2


def call_ollama_json(
    host: str,
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float = 0.3,
    agent_label: str = "agent",
) -> dict:
    """Ollama'ya JSON-formatlı bir istek atar, parse hatasında retry yapar.

    Retry stratejisi: parse başarısızsa, modele son çıktısının bozuk olduğunu
    söyleyip sadece geçerli JSON döndürmesini hatırlatan bir ek mesajla tekrar dener.
    MAX_RETRIES aşılırsa, son hatayı (ham çıktıyla birlikte) fırlatır.
    """
    client = ollama.Client(host=host)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    last_error = None
    last_raw = None

    for attempt in range(MAX_RETRIES + 1):
        response = client.chat(
            model=model,
            messages=messages,
            format="json",
            options={"temperature": temperature},
        )
        raw_text = response["message"]["content"].strip()
        last_raw = raw_text

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            last_error = e
            if attempt < MAX_RETRIES:
                # Modele hatasını göster, kısa ve net bir düzeltme iste — konuşmayı
                # büyütmeden (context şişmesi tekrar bozulmaya yol açabilir) sadece
                # son mesajı ekleyip tekrar dene.
                messages = messages[:2] + [
                    {"role": "assistant", "content": raw_text[:500]},
                    {"role": "user", "content": (
                        "Bu çıktı geçersiz JSON. SADECE geçerli, eksiksiz bir JSON nesnesi "
                        "döndür — başka hiçbir metin, açıklama veya farklı bir dilde yazı ekleme."
                    )},
                ]

    raise ValueError(
        f"{agent_label} ({model}) {MAX_RETRIES + 1} denemede de geçerli JSON döndüremedi: "
        f"{last_error}\nSon ham çıktı: {last_raw}"
    )
