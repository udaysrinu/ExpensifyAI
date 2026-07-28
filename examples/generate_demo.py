"""Generate a demo dashboard from synthetic data — safe to commit and share.

No real Splitwise data, names, or balances. Produces examples/demo-dashboard.html
so anyone evaluating ExpensifyAI can see the output without credentials.

    python examples/generate_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from splitwise_mcp_server import analytics as A          # noqa: E402
from splitwise_mcp_server import dashboard as D          # noqa: E402

# Synthetic group members
YOU, RIYA, KABIR, MEERA = 1, 2, 3, 4
NAMES = {YOU: "You", RIYA: "Riya", KABIR: "Kabir", MEERA: "Meera"}


def user(uid, paid, owed):
    return {
        "user_id": uid,
        "user": {"id": uid, "first_name": NAMES[uid], "last_name": ""},
        "paid_share": f"{paid:.2f}",
        "owed_share": f"{owed:.2f}",
    }


def expense(eid, desc, cost, cat, date, payers, split_among=(YOU, RIYA, KABIR, MEERA)):
    """payers: {uid: paid}. Cost split equally among split_among."""
    share = round(cost / len(split_among), 2)
    users = []
    for uid in (YOU, RIYA, KABIR, MEERA):
        paid = payers.get(uid, 0.0)
        owed = share if uid in split_among else 0.0
        if paid or owed:
            users.append(user(uid, paid, owed))
    # fix rounding drift on the last owed share so shares sum to cost
    total_owed = sum(float(u["owed_share"]) for u in users)
    drift = round(cost - total_owed, 2)
    if drift and users:
        last = next(u for u in reversed(users) if float(u["owed_share"]) > 0)
        last["owed_share"] = f"{float(last['owed_share']) + drift:.2f}"
    return {
        "id": eid, "description": desc, "cost": f"{cost:.2f}", "currency_code": "INR",
        "category": {"id": 1, "name": cat}, "date": date, "payment": False,
        "deleted_at": None, "users": users,
    }


# A believable few months of shared-flat spending
DATA = [
    expense(1, "Monthly groceries — BigBasket", 4200, "Groceries", "2026-01-04T10:00:00Z", {YOU: 4200}),
    expense(2, "Electricity bill — Jan", 2800, "Utilities", "2026-01-07T10:00:00Z", {RIYA: 2800}),
    expense(3, "House rent — January", 48000, "Rent", "2026-01-01T10:00:00Z", {YOU: 48000}),
    expense(4, "Internet — Airtel Fiber", 1099, "TV/Phone/Internet", "2026-01-05T10:00:00Z", {KABIR: 1099}),
    expense(5, "Dinner out — Toit", 3600, "Dining out", "2026-01-12T20:00:00Z", {MEERA: 3600}),
    expense(6, "Cleaning supplies", 890, "Household supplies", "2026-01-09T10:00:00Z", {YOU: 890}),
    expense(7, "Weekend groceries — Zepto", 1650, "Groceries", "2026-01-18T10:00:00Z", {RIYA: 1650}),
    expense(8, "Cook salary — January", 6000, "Services", "2026-01-31T10:00:00Z", {YOU: 6000}),
    expense(9, "Gas cylinder", 1150, "Utilities", "2026-01-22T10:00:00Z", {KABIR: 1150}),
    expense(10, "Movie night — PVR", 1400, "Entertainment", "2026-01-25T20:00:00Z", {MEERA: 1400}),

    expense(11, "House rent — February", 48000, "Rent", "2026-02-01T10:00:00Z", {YOU: 48000}),
    expense(12, "Monthly groceries — BigBasket", 4500, "Groceries", "2026-02-05T10:00:00Z", {RIYA: 4500}),
    expense(13, "Electricity bill — Feb", 3100, "Utilities", "2026-02-07T10:00:00Z", {YOU: 3100}),
    expense(14, "Internet — Airtel Fiber", 1099, "TV/Phone/Internet", "2026-02-05T10:00:00Z", {KABIR: 1099}),
    expense(15, "Cook salary — February", 6000, "Services", "2026-02-28T10:00:00Z", {YOU: 6000}),
    expense(16, "Birthday dinner — Farzi Cafe", 5200, "Dining out", "2026-02-14T20:00:00Z", {MEERA: 5200}),
    expense(17, "Groceries — Zepto", 2100, "Groceries", "2026-02-19T10:00:00Z", {RIYA: 2100}),
    expense(18, "Water can subscription", 600, "Household supplies", "2026-02-10T10:00:00Z", {KABIR: 600}),

    expense(19, "House rent — March", 48000, "Rent", "2026-03-01T10:00:00Z", {YOU: 48000}),
    expense(20, "Holi party supplies", 3400, "Entertainment", "2026-03-14T18:00:00Z", {MEERA: 3400}),
    expense(21, "Monthly groceries — BigBasket", 4800, "Groceries", "2026-03-05T10:00:00Z", {YOU: 4800}),
    expense(22, "Electricity bill — Mar", 3600, "Utilities", "2026-03-07T10:00:00Z", {RIYA: 3600}),
    expense(23, "Cook salary — March", 6000, "Services", "2026-03-31T10:00:00Z", {YOU: 6000}),
    expense(24, "Internet — Airtel Fiber", 1099, "TV/Phone/Internet", "2026-03-05T10:00:00Z", {KABIR: 1099}),
    expense(25, "Plumber repair", 1200, "Maintenance", "2026-03-20T10:00:00Z", {KABIR: 1200}),
    expense(26, "Dinner — Meghana Foods", 2800, "Dining out", "2026-03-22T20:00:00Z", {RIYA: 2800}),
]


def main():
    # Deterministic server-side compute (source of truth) — used to sanity-check.
    result = A.compute_analytics(
        DATA, current_user_id=YOU, target_type="group", target_id=101,
        target_label="Flat 4B · Demo",
    )
    # Interactive dashboard renders from the embedded dataset (client re-filters live).
    dataset = A.build_dataset(
        DATA, current_user_id=YOU, target_type="group", target_id=101,
        target_label="Flat 4B · Demo",
    )
    out = Path(__file__).resolve().parent / "demo-dashboard.html"
    out.write_text(D.render_interactive(dataset), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"  reconciled: {result['reconciliation']['reconciled']}")
    print(f"  group total: {result['category_breakdown']['total']} INR")
    print(f"  settlement txns: {result['settlement']['transaction_count']}")
    print(f"  verify_total_paise: {dataset['meta']['verify_total_paise']}")


if __name__ == "__main__":
    main()
