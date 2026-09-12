"""
Step 2b: Populate places.product_fit based on primary_type.

Values:
  appointment  — calendar-based scheduling (beauty, health excl. pharmacy, fitness,
                 auto only for car_repair / car_wash / tire_shop)
  booking      — table / venue reservation (sit-down restaurants, bars, lodging,
                 banquet & event venues)
  orders_menu  — pre-order / menu flow (cafes, bakeries, fast-food, takeaway)
  none         — no booking product fit (retail auto, pharmacy, stadiums, excluded)
"""
from __future__ import annotations
import sqlite3
from config import DB_PATH

# primary_type → product_fit
_FIT: dict[str, str] = {
    # ── appointment: beauty ─────────────────────────────────────────────────
    "beauty_salon":      "appointment",
    "hair_salon":        "appointment",
    "nail_salon":        "appointment",
    "barber_shop":       "appointment",
    "spa":               "appointment",
    "massage_spa":       "appointment",
    "massage":           "appointment",
    "sauna":             "appointment",
    "skin_care_clinic":  "appointment",
    "cosmetics_store":   "appointment",
    "beautician":        "appointment",
    "tanning_studio":    "appointment",
    "makeup_artist":     "appointment",
    "wellness_center":   "appointment",
    # ── appointment: health (pharmacy excluded) ──────────────────────────────
    "doctor":            "appointment",
    "dentist":           "appointment",
    "medical_clinic":    "appointment",
    "dental_clinic":     "appointment",
    "medical_center":    "appointment",
    "medical_lab":       "appointment",
    "hospital":          "appointment",
    "physiotherapist":   "appointment",
    "chiropractor":      "appointment",
    "veterinary_care":   "appointment",
    "health":            "appointment",
    "pharmacy":          "none",
    # ── appointment: fitness ─────────────────────────────────────────────────
    "gym":                       "appointment",
    "fitness_center":            "appointment",
    "yoga_studio":               "appointment",
    "sports_club":               "appointment",
    "sports_complex":            "appointment",
    "sports_school":             "appointment",
    "sports_coaching":           "appointment",
    "sports_activity_location":  "appointment",
    "swimming_pool":             "appointment",
    # ── appointment: auto (service only) ─────────────────────────────────────
    "car_repair":        "appointment",
    "car_wash":          "appointment",
    "tire_shop":         "appointment",
    # ── none: auto retail / transport ────────────────────────────────────────
    "car_dealer":            "none",
    "auto_parts_store":      "none",
    "gas_station":           "none",
    "car_rental":            "none",
    "truck_dealer":          "none",
    "chauffeur_service":     "none",
    "taxi_service":          "none",
    "transportation_service": "none",
    # ── booking: sit-down restaurants & bars ────────────────────────────────
    "restaurant":                   "booking",
    "bar":                          "booking",
    "pub":                          "booking",
    "brewpub":                      "booking",
    "sports_bar":                   "booking",
    "cocktail_bar":                 "booking",
    "lounge_bar":                   "booking",
    "wine_bar":                     "booking",
    "beer_garden":                  "booking",
    "hookah_bar":                   "booking",
    "bar_and_grill":                "booking",
    "gastropub":                    "booking",
    "tea_house":                    "booking",
    "bistro":                       "booking",
    "family_restaurant":            "booking",
    "fine_dining_restaurant":       "booking",
    "dessert_restaurant":           "booking",
    "breakfast_restaurant":         "booking",
    "brunch_restaurant":            "booking",
    "diner":                        "booking",
    "romanian_restaurant":          "booking",
    "turkish_restaurant":           "booking",
    "italian_restaurant":           "booking",
    "european_restaurant":          "booking",
    "eastern_european_restaurant":  "booking",
    "asian_restaurant":             "booking",
    "japanese_restaurant":          "booking",
    "korean_restaurant":            "booking",
    "chinese_restaurant":           "booking",
    "indian_restaurant":            "booking",
    "north_indian_restaurant":      "booking",
    "indonesian_restaurant":        "booking",
    "middle_eastern_restaurant":    "booking",
    "mediterranean_restaurant":     "booking",
    "french_restaurant":            "booking",
    "american_restaurant":          "booking",
    "western_restaurant":           "booking",
    "czech_restaurant":             "booking",
    "greek_restaurant":             "booking",
    "lebanese_restaurant":          "booking",
    "israeli_restaurant":           "booking",
    "mexican_restaurant":           "booking",
    "colombian_restaurant":         "booking",
    "ukrainian_restaurant":         "booking",
    "russian_restaurant":           "booking",
    "barbecue_restaurant":          "booking",
    "steak_house":                  "booking",
    "seafood_restaurant":           "booking",
    "hamburger_restaurant":         "booking",
    "chicken_restaurant":           "booking",
    "chicken_wings_restaurant":     "booking",
    "hot_pot_restaurant":           "booking",
    "ramen_restaurant":             "booking",
    "soup_restaurant":              "booking",
    "irish_pub":                    "booking",
    "brewery":                      "booking",
    # ── booking: lodging ─────────────────────────────────────────────────────
    "hotel":               "booking",
    "resort_hotel":        "booking",
    "extended_stay_hotel": "booking",
    "bed_and_breakfast":   "booking",
    "guest_house":         "booking",
    "hostel":              "booking",
    "lodging":             "booking",
    # ── booking: events & venues ─────────────────────────────────────────────
    "banquet_hall":             "booking",
    "event_venue":              "booking",
    "wedding_venue":            "booking",
    "convention_center":        "booking",
    "concert_hall":             "booking",
    "performing_arts_theater":  "booking",
    "opera_house":              "booking",
    "amphitheatre":             "booking",
    "live_music_venue":         "booking",
    # ── none: spectator / amusement venues ───────────────────────────────────
    "stadium":          "none",
    "arena":            "none",
    "community_center": "none",
    "cultural_center":  "none",
    "amusement_center": "none",
    "dance_hall":       "none",
    "go_karting_venue": "none",
    "race_course":      "none",
    "casino":           "none",
    # ── orders_menu: quick-service food ──────────────────────────────────────
    "coffee_shop":          "orders_menu",
    "cafe":                 "orders_menu",
    "fast_food_restaurant": "orders_menu",
    "bakery":               "orders_menu",
    "pastry_shop":          "orders_menu",
    "pizza_restaurant":     "orders_menu",
    "sushi_restaurant":     "orders_menu",
    "kebab_shop":           "orders_menu",
    "shawarma_restaurant":  "orders_menu",
    "meal_takeaway":        "orders_menu",
    "meal_delivery":        "orders_menu",
    "pizza_delivery":       "orders_menu",
    "buffet_restaurant":    "orders_menu",
    "cafeteria":            "orders_menu",
    "snack_bar":            "orders_menu",
    "coffee_stand":         "orders_menu",
    "dessert_shop":         "orders_menu",
    "cake_shop":            "orders_menu",
    "confectionery":        "orders_menu",
    "ice_cream_shop":       "orders_menu",
    # ── none: excluded vertical ───────────────────────────────────────────────
    "store":                    "none",
    "service":                  "none",
    "supermarket":              "none",
    "grocery_store":            "none",
    "food_store":               "none",
    "convenience_store":        "none",
    "general_store":            "none",
    "market":                   "none",
    "liquor_store":             "none",
    "butcher_shop":             "none",
    "pet_store":                "none",
    "pet_care":                 "none",
    "pet_boarding_service":     "none",
    "home_goods_store":         "none",
    "clothing_store":           "none",
    "jewelry_store":            "none",
    "gift_shop":                "none",
    "building_materials_store": "none",
    "shopping_mall":            "none",
    "wholesaler":               "none",
    "supplier":                 "none",
    "manufacturer":             "none",
    "corporate_office":         "none",
    "real_estate_agency":       "none",
    "educational_institution":  "none",
    "non_profit_organization":  "none",
    "consultant":               "none",
    "general_contractor":       "none",
    "locksmith":                "none",
    "catering_service":         "none",
    "laundry":                  "none",
    "storage":                  "none",
    "internet_cafe":            "none",
    "art_studio":               "none",
    "historical_landmark":      "none",
    "synagogue":                "none",
    "preschool":                "none",
}


def run() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("ALTER TABLE places ADD COLUMN product_fit TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Apply mapping by primary_type
    for primary_type, fit in _FIT.items():
        conn.execute(
            "UPDATE places SET product_fit = ? WHERE primary_type = ?",
            (fit, primary_type),
        )

    # For null primary_type: infer from types_json using same _FIT dict
    import json
    null_rows = conn.execute(
        "SELECT place_id, types_json FROM places WHERE primary_type IS NULL"
    ).fetchall()
    for row in null_rows:
        types = json.loads(row["types_json"] or "[]")
        fit = next((f for t in types if (f := _FIT.get(t))), "none")
        conn.execute(
            "UPDATE places SET product_fit = ? WHERE place_id = ?",
            (fit, row["place_id"]),
        )

    conn.commit()

    # Validate: no nulls remain
    null_count = conn.execute(
        "SELECT COUNT(*) FROM places WHERE product_fit IS NULL"
    ).fetchone()[0]
    if null_count:
        missing = conn.execute(
            "SELECT DISTINCT primary_type FROM places WHERE product_fit IS NULL LIMIT 20"
        ).fetchall()
        raise ValueError(f"{null_count} unclassified — missing types: {[r[0] for r in missing]}")

    # Summary
    rows = conn.execute("""
        SELECT product_fit, COUNT(*) AS n
        FROM places
        GROUP BY product_fit
        ORDER BY n DESC
    """).fetchall()

    print(f"\n{'product_fit':<14} {'places':>7}  {'%':>6}")
    print("-" * 30)
    total = sum(r["n"] for r in rows)
    for r in rows:
        pct = r["n"] / total * 100
        print(f"{r['product_fit']:<14} {r['n']:>7}  {pct:>5.1f}%")
    print("-" * 30)
    print(f"{'TOTAL':<14} {total:>7}")

    conn.close()


if __name__ == "__main__":
    run()
