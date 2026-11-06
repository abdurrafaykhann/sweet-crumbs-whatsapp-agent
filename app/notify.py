"""Messages the shop sends on its own (status changes, payment checks), in the customer's language: English, Roman Urdu or Urdu script.
Fixed wording on purpose: these are operational messages that must be exact, instant and free of model calls."""
from __future__ import annotations

TEXT = {
    "preparing": {
        "en": "Good news! Your order {code} is now being prepared in our kitchen.",
        "rur": "Khushkhabri! Aap ka order {code} ab kitchen mein tayyar ho raha hai.",
        "ur": "خوشخبری! آپ کا آرڈر {code} اب کچن میں تیار ہو رہا ہے۔"},
    "out_for_delivery": {
        "en": "Your order {code} is out for delivery with our rider {rider}. Expected around {eta}.",
        "rur": "Aap ka order {code} rider {rider} ke paas hai, raste mein hai. Taqreeban {eta} tak pohanch jayega.",
        "ur": "آپ کا آرڈر {code} رائیڈر {rider} کے پاس ہے اور راستے میں ہے۔ تقریباً {eta} تک پہنچ جائے گا۔"},
    "ready_for_pickup": {
        "en": "Your order {code} is ready for pickup at our {branch} branch.",
        "rur": "Aap ka order {code} {branch} branch par pickup ke liye tayyar hai.",
        "ur": "آپ کا آرڈر {code} {branch} برانچ پر پک اپ کے لیے تیار ہے۔"},
    "delivered": {
        "en": "Your order {code} has been delivered. Thank you for choosing Sweet Crumbs! {points_en}",
        "rur": "Aap ka order {code} deliver ho gaya hai. Sweet Crumbs choose karne ka shukriya! {points_rur}",
        "ur": "آپ کا آرڈر {code} ڈیلیور ہو گیا ہے۔ سویٹ کرمبز کا انتخاب کرنے کا شکریہ! {points_ur}"},
    "cancelled": {
        "en": "Your order {code} has been cancelled. If you did not expect this, please reply here and we will help.",
        "rur": "Aap ka order {code} cancel ho gaya hai. Agar aap ko iski umeed nahi thi to yahan jawab dein, hum madad karenge.",
        "ur": "آپ کا آرڈر {code} منسوخ ہو گیا ہے۔ اگر آپ کو اس کی توقع نہیں تھی تو یہاں جواب دیں، ہم مدد کریں گے۔"},
    "advance_confirmed": {
        "en": "Thank you! We have received your advance of Rs {amount}. Your order {code} is confirmed.",
        "rur": "Shukriya! Aap ka Rs {amount} ka advance mil gaya hai. Aap ka order {code} confirm ho gaya hai.",
        "ur": "شکریہ! آپ کا Rs {amount} کا ایڈوانس مل گیا ہے۔ آپ کا آرڈر {code} کنفرم ہو گیا ہے۔"},
    "advance_rejected": {
        "en": "We could not verify your payment for order {code}. Please send a clear screenshot of the payment or reply here and we will help.",
        "rur": "Order {code} ki payment verify nahi ho saki. Barah-e-karam saaf screenshot dobara bhejein ya yahan jawab dein, hum madad karenge.",
        "ur": "آرڈر {code} کی ادائیگی کی تصدیق نہیں ہو سکی۔ براہ کرم واضح اسکرین شاٹ دوبارہ بھیجیں یا یہاں جواب دیں، ہم مدد کریں گے۔"},
}


def status_text(kind: str, lang: str, **fields) -> str:
    lang = lang if lang in ("en", "rur", "ur") else "en"
    points = int(fields.get("points") or 0)
    fields = {**fields,
              "points_en": f"You earned {points} Crumbs Club points." if points else "",
              "points_rur": f"Aap ko {points} Crumbs Club points mile." if points else "",
              "points_ur": f"آپ کو {points} کرمبز کلب پوائنٹس ملے۔" if points else ""}
    return TEXT[kind][lang].format(**fields).strip()
