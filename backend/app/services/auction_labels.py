"""拍卖行 replicate 中与客户端 Enum.AuctionHouseTimeLeftBand 一致的档位说明（见 Warcraft Wiki GetTimeLeftBandInfo）。"""

from typing import Any

# 值 0–3 与 C_AuctionHouse.GetReplicateItemTimeLeft / GetTimeLeftBandInfo 文档一致
_TIME_LEFT_BAND_ZH: dict[int, str] = {
    0: "短档 (约 30 分钟内)",
    1: "中档 (约 2 小时级)",
    2: "长档 (约 12 小时级)",
    3: "很长档 (约 48 小时 / 2 天级)",
}


def label_time_left_band(band: Any) -> str | None:
    if band is None:
        return None
    try:
        i = int(band)
    except (TypeError, ValueError):
        return str(band)
    return _TIME_LEFT_BAND_ZH.get(i, f"档位={i}")


def enrich_auction_item_dict(d: dict[str, Any]) -> dict[str, Any]:
    """为单条拍卖字典增加 timeLeftLabel，便于 JSON 输出。"""
    out = dict(d)
    tl = out.get("timeLeftBand")
    out["timeLeftLabel"] = label_time_left_band(tl)
    return out
