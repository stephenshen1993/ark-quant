"""Verified corporate-action cutovers for current valuation and order estimates."""


def adjust_cb_reference_price(code, price, price_date, valuation_date):
    # Issuer announcement: https://paper.cnstock.com/html/2026-09/22/content_2271342.htm
    # Match the known stale snapshot exactly; never modify historical source data
    # or deduct a coupon again from a quote on/after the ex-interest date.
    if (code == "127046" and price == 124.724 and price_date == "2026-09-28"
            and valuation_date >= "2026-09-29"):
        return 122.924, "百润转债：9月28日原始价124.724按每张付息1.80元调整为122.924，仅用于除息后估值及交易金额估算，不是委托限价。"
    return price, None
