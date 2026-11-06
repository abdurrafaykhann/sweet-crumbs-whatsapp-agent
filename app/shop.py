"""Everything the agent knows about the business lives here. Edit this one file to adapt the agent to a real shop.
All names, the address, phone numbers, prices and policies below are FICTIONAL DEMO DATA: replace them before real use."""

SHOP = {
    "name": "Sweet Crumbs Bakery",
    "address": "Shop 7, Main Boulevard, Block 13-D, Gulshan-e-Iqbal, Karachi",
    "area_hint": "Gulshan-e-Iqbal, Karachi",
    "open": "09:00",   # 24h clock, Pakistan time
    "close": "22:00",
    "hours_text": "Every day, 9:00 am to 10:00 pm",
    "team_name": "our team",
    "team_phone": "+92 300 0000000",   # DEMO PLACEHOLDER: replace with the real team number
    "team_phone_hours": "9:00 am to 10:00 pm, call or WhatsApp",
    "delivery_areas": "Gulshan-e-Iqbal, Gulistan-e-Jauhar, North Nazimabad, PECHS and Clifton",
    "delivery_fee": 150,
    "free_delivery_over": 3000,
    "delivery_extra_minutes": 60,   # delivery takes this long after the item is ready
    "payment": "Orders under Rs 4000: cash on delivery or at pickup, or pay online. Orders of Rs 4000 and above (all 3 lb cakes) need a 50% advance online; the balance is paid in cash on delivery or at pickup. Customers can also pay the full amount online.",
    "delivery_area_keys": ["gulshan", "jauhar", "nazimabad", "pechs", "clifton"],   # an address must mention one of these
    "policies": [
        "Custom message on any cake is free. Candles and a knife are included.",
        "Cakes are made fresh to order: 1 lb needs about 6 hours notice, 2 lb about 12 hours, 3 lb about 24 hours.",
        "Cakes are halal and made without alcohol.",
        "Prices are fixed (no discounts). For events above 50 pieces or more than 3 cakes, custom designs or photo cakes, the team prepares a quote.",
        "We cannot share photos in this chat; the team can on the contact number.",
        "Eggless cakes and photo cakes are not available right now.",
        "Delivery is available only in the listed areas; for other areas the team confirms.",
        "Address, time or message changes are possible until baking starts; after that please call the team.",
        "Refunds of paid amounts are handled by the team.",
    ],
}



# ---------------------------------------------------------------- payments (ALL ACCOUNT DETAILS ARE FICTIONAL PLACEHOLDERS: replace)
PAYMENT = {
    "advance_percent": 50,
    "advance_required_from": 4000,   # orders with total at or above this need the advance online
    "hold_minutes": 120,             # an unpaid reservation is released after this
    "verify_minutes": "10 to 15 minutes while the shop is open",
    "methods": [
        {"name": "Easypaisa", "account_title": "Sweet Crumbs Bakery", "number": "0300 0000000"},
        {"name": "JazzCash", "account_title": "Sweet Crumbs Bakery", "number": "0300 0000001"},
        {"name": "Bank transfer (Meezan Bank)", "account_title": "Sweet Crumbs Bakery", "account_no": "0000-0000000000",
         "iban": "PK00MEZN0000000000000000"},
    ],
    "how": "Send the exact amount, then share a screenshot of the payment (or the transaction ID) in this chat. "
           "Our team verifies it and the order is confirmed once they approve it.",
}

# status flow shown to customers; the shop (or dashboard) moves an order along it
STATUS_TEXT = {
    "awaiting_payment": "Your order is reserved and waiting for payment. It is confirmed only after our team verifies the payment.",
    "payment_submitted": "We have your payment proof and our team is verifying it. You will get a message here as soon as it is confirmed.",
    "confirmed": "Your order is confirmed and scheduled. Baking will start in time for your requested slot.",
    "preparing": "Our bakers are preparing your order right now.",
    "ready": "Your order is ready.",
    "out_for_delivery": "Your order is out for delivery with our rider.",
    "delivered": "Your order has been delivered.",
    "cancelled": "This order was cancelled.",
}
STATUSES = list(STATUS_TEXT)

# Each product: variations are what the customer actually chooses (size / flavour / filling).
# stock = units available per day for that variation; lead_min = minimum minutes from order to ready.
MENU = [
    {
        "name": "Chocolate Fudge Cake", "category": "Cakes",
        "aliases": "chocolate cake,choco cake,chocolate fudge,fudge cake,چاکلیٹ کیک",
        "description": "Our best seller: three layers of moist dark-chocolate sponge, filled and covered in silky chocolate ganache.",
        "ingredients": "Flour, eggs, sugar, butter, cocoa powder, dark chocolate, fresh cream, vanilla",
        "allergens": "Eggs, milk, gluten. May contain traces of nuts.",
        "storage": "Keep refrigerated and eat within 3 days. Take out 20 minutes before serving.",
        "variations": [
            {"name": "1 lb", "price": 1900, "stock": 5, "lead_min": 360, "note": "serves 6 to 8"},
            {"name": "2 lb", "price": 3600, "stock": 4, "lead_min": 720, "note": "serves 12 to 15"},
            {"name": "3 lb", "price": 5200, "stock": 2, "lead_min": 1440, "note": "serves 20 to 25"},
        ],
    },
    {
        "name": "Strawberry Vanilla Cream Cake", "category": "Cakes",
        "aliases": "strawberry cake,vanilla cake,cream cake,strawberry vanilla,سٹرابیری کیک,ونیلا کیک",
        "description": "Light vanilla sponge with fresh whipped cream and strawberry compote. A favourite for kids' birthdays.",
        "ingredients": "Flour, eggs, sugar, butter, vanilla, fresh whipped cream, strawberry compote",
        "allergens": "Eggs, milk, gluten.",
        "storage": "Keep refrigerated and eat within 2 days.",
        "variations": [
            {"name": "1 lb", "price": 1700, "stock": 5, "lead_min": 360, "note": "serves 6 to 8"},
            {"name": "2 lb", "price": 3200, "stock": 4, "lead_min": 720, "note": "serves 12 to 15"},
            {"name": "3 lb", "price": 4700, "stock": 2, "lead_min": 1440, "note": "serves 20 to 25"},
        ],
    },
    {
        "name": "Red Velvet Cake", "category": "Cakes",
        "aliases": "red velvet,redvelvet,ریڈ ویلویٹ",
        "description": "Soft red sponge with a mild cocoa taste, layered with cream cheese frosting.",
        "ingredients": "Flour, eggs, sugar, buttermilk, cocoa, vegetable oil, red food colour, cream cheese, butter",
        "allergens": "Eggs, milk, gluten.",
        "storage": "Keep refrigerated and eat within 3 days.",
        "variations": [
            {"name": "1 lb", "price": 2100, "stock": 4, "lead_min": 360, "note": "serves 6 to 8"},
            {"name": "2 lb", "price": 4000, "stock": 3, "lead_min": 720, "note": "serves 12 to 15"},
        ],
    },
    {
        "name": "Pineapple Cake", "category": "Cakes",
        "aliases": "pineapple,pineapple cake,انناس کیک,پائن ایپل",
        "description": "The classic birthday cake: vanilla sponge, whipped cream and juicy pineapple pieces.",
        "ingredients": "Flour, eggs, sugar, butter, vanilla, fresh whipped cream, pineapple pieces",
        "allergens": "Eggs, milk, gluten.",
        "storage": "Keep refrigerated and eat within 2 days.",
        "variations": [
            {"name": "1 lb", "price": 1600, "stock": 5, "lead_min": 360, "note": "serves 6 to 8"},
            {"name": "2 lb", "price": 3000, "stock": 4, "lead_min": 720, "note": "serves 12 to 15"},
            {"name": "3 lb", "price": 4400, "stock": 2, "lead_min": 1440, "note": "serves 20 to 25"},
        ],
    },
    {
        "name": "Black Forest Cake", "category": "Cakes",
        "aliases": "black forest,blackforest,بلیک فاریسٹ",
        "description": "Chocolate sponge with whipped cream, cherries and chocolate shavings. No alcohol is used.",
        "ingredients": "Flour, eggs, sugar, butter, cocoa, fresh whipped cream, cherries, dark chocolate shavings",
        "allergens": "Eggs, milk, gluten. May contain traces of nuts.",
        "storage": "Keep refrigerated and eat within 2 days.",
        "variations": [
            {"name": "1 lb", "price": 1900, "stock": 4, "lead_min": 360, "note": "serves 6 to 8"},
            {"name": "2 lb", "price": 3600, "stock": 3, "lead_min": 720, "note": "serves 12 to 15"},
        ],
    },
    {
        "name": "Cupcakes", "category": "Cupcakes & Brownies",
        "aliases": "cupcake,cupcakes,cup cake,کپ کیک",
        "description": "Soft individual cupcakes with buttercream swirl. Sold per piece; order 6 or 12 for a gift box.",
        "ingredients": "Flour, eggs, sugar, butter, milk, vanilla, buttercream (butter, icing sugar)",
        "allergens": "Eggs, milk, gluten.",
        "storage": "Best the same day; keep cool and eat within 2 days.",
        "variations": [
            {"name": "Vanilla", "price": 150, "stock": 40, "lead_min": 180, "note": "per piece"},
            {"name": "Chocolate", "price": 170, "stock": 40, "lead_min": 180, "note": "per piece"},
            {"name": "Red Velvet", "price": 200, "stock": 24, "lead_min": 180, "note": "per piece, cream cheese frosting"},
        ],
    },
    {
        "name": "Brownies", "category": "Cupcakes & Brownies",
        "aliases": "brownie,brownies,براؤنی",
        "description": "Dense, fudgy chocolate brownies baked in the morning. Sold per piece (about 90 g).",
        "ingredients": "Dark chocolate, butter, sugar, eggs, flour, cocoa powder",
        "allergens": "Eggs, milk, gluten. Classic Walnut contains walnuts; Nutella-Stuffed contains hazelnuts.",
        "storage": "Keep at room temperature up to 2 days, or refrigerate for 5 days.",
        "variations": [
            {"name": "Plain Fudge", "price": 200, "stock": 25, "lead_min": 60, "note": "per piece, nut-free recipe"},
            {"name": "Classic Walnut", "price": 220, "stock": 25, "lead_min": 60, "note": "per piece, with walnuts"},
            {"name": "Nutella-Stuffed", "price": 300, "stock": 15, "lead_min": 60, "note": "per piece, hazelnut filling"},
        ],
    },
    {
        "name": "Butter Croissant", "category": "Bakery",
        "aliases": "croissant,croissants,کروسینٹ",
        "description": "Flaky, golden croissants made with real butter and baked fresh every morning.",
        "ingredients": "Flour, butter, milk, sugar, yeast, salt, egg wash. Fillings: dark chocolate or almond cream",
        "allergens": "Gluten, milk, eggs. Almond version contains almonds.",
        "storage": "Best the same day. Warm 3 minutes in an oven to refresh.",
        "variations": [
            {"name": "Plain Butter", "price": 250, "stock": 12, "lead_min": 60, "note": "per piece"},
            {"name": "Chocolate", "price": 300, "stock": 10, "lead_min": 60, "note": "per piece"},
            {"name": "Almond", "price": 350, "stock": 8, "lead_min": 60, "note": "per piece"},
        ],
    },
    {
        "name": "Cookies Box", "category": "Bakery",
        "aliases": "cookies,cookie,biscuit,biscuits,nankhatai,کوکیز,بسکٹ,نان خطائی",
        "description": "A gift box of 12 freshly baked cookies.",
        "ingredients": "Chocolate Chip: flour, butter, brown sugar, eggs, chocolate chips. Oatmeal Raisin: oats, flour, butter, raisins, cinnamon. Nankhatai: flour, gram flour, ghee, sugar, cardamom",
        "allergens": "Gluten and milk in all. Eggs in Chocolate Chip and Oatmeal Raisin. Nankhatai has no eggs and no nuts.",
        "storage": "Airtight container, stays fresh for 7 days.",
        "variations": [
            {"name": "Chocolate Chip", "price": 850, "stock": 8, "lead_min": 60, "note": "box of 12"},
            {"name": "Oatmeal Raisin", "price": 800, "stock": 0, "lead_min": 60, "note": "box of 12"},
            {"name": "Nankhatai", "price": 650, "stock": 10, "lead_min": 60, "note": "box of 12"},
        ],
    },
    {
        "name": "Bread Loaf", "category": "Bakery",
        "aliases": "bread,loaf,bread loaf,sandwich bread,double roti,ڈبل روٹی,بریڈ",
        "description": "Freshly baked loaf, about 600 g, baked twice a day.",
        "ingredients": "White: flour, yeast, salt, sugar, oil. Whole Wheat: whole wheat flour, yeast, salt, honey, oil. Milk Bread: flour, milk, butter, sugar, yeast",
        "allergens": "Gluten. Milk Bread also contains milk and butter. White and Whole Wheat are egg-free.",
        "storage": "Keep in a bag at room temperature for 3 days.",
        "variations": [
            {"name": "White Sandwich", "price": 180, "stock": 30, "lead_min": 60, "note": "about 600 g"},
            {"name": "Whole Wheat", "price": 230, "stock": 20, "lead_min": 60, "note": "about 600 g"},
            {"name": "Milk Bread", "price": 210, "stock": 20, "lead_min": 60, "note": "about 600 g"},
        ],
    },
    {
        "name": "Patties", "category": "Savoury",
        "aliases": "patties,patty,puff,chicken patty,chicken patties,پیٹیز,پف",
        "description": "Crisp puff-pastry patties, baked fresh. Sold per piece; great for parties.",
        "ingredients": "Puff pastry (flour, butter), filling of spiced chicken or potato and peas; cheese version has mozzarella",
        "allergens": "Gluten, milk. Egg wash on top.",
        "storage": "Eat warm. Reheat 5 minutes in an oven; keep refrigerated up to 2 days.",
        "variations": [
            {"name": "Chicken", "price": 160, "stock": 40, "lead_min": 45, "note": "per piece"},
            {"name": "Chicken Cheese", "price": 190, "stock": 30, "lead_min": 45, "note": "per piece"},
            {"name": "Aloo Veg", "price": 130, "stock": 30, "lead_min": 45, "note": "per piece, vegetarian"},
        ],
    },
]
