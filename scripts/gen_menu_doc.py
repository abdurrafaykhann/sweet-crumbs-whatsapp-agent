"""Generate docs/MENU.md from app/shop.py so the documentation always matches what the agent knows."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.shop import MENU, SHOP  # noqa: E402

out = [f"# {SHOP['name']}: demo menu and shop facts\n",
       "> Fictional demo data. Edit `app/shop.py` (the agent, dashboard and this page all follow it), then run `python scripts/gen_menu_doc.py`.\n",
       "## Shop facts the agent knows",
       f"- **Address:** {SHOP['address']}", f"- **Hours:** {SHOP['hours_text']}",
       f"- **Team contact:** {SHOP['team_phone']} ({SHOP['team_phone_hours']})",
       f"- **Delivery:** {SHOP['delivery_areas']}. Rs {SHOP['delivery_fee']}, free from Rs {SHOP['free_delivery_over']}. About {SHOP['delivery_extra_minutes']} min after the order is ready.",
       f"- **Payment:** {SHOP['payment']}", "- **Policies:**"]
out += [f"  - {p}" for p in SHOP["policies"]]
out.append("\n## Menu (%d items)\n" % len(MENU))
for p in MENU:
    out.append(f"### {p['name']}  _({p['category']})_")
    out.append(p["description"] + "\n")
    out.append(f"- **Ingredients:** {p['ingredients']}")
    out.append(f"- **Allergens:** {p['allergens']}")
    out.append(f"- **Storage:** {p['storage']}\n")
    out.append("| Variation | Price (PKR) | Note | Min. notice |\n|---|---|---|---|")
    for v in p["variations"]:
        lead = f"{v['lead_min'] // 60} h" if v["lead_min"] >= 60 else f"{v['lead_min']} min"
        out.append(f"| {v['name']} | {v['price']} | {v['note']} | {lead} |")
    out.append("")
open(os.path.join(os.path.dirname(__file__), "..", "docs", "MENU.md"), "w", encoding="utf-8").write("\n".join(out))
print("docs/MENU.md written")
