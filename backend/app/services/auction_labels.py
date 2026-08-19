"""拍卖行 replicate 接口返回的剩余时间档位说明。"""

from typing import Any

# GetReplicateItemTimeLeft 沿用旧拍卖 API 的 1–4 值；它不是新版枚举的 0–3。
_TIME_LEFT_BAND_ZH: dict[int, str] = {
    1: "短档 (约 30 分钟内)",
    2: "中档 (约 2 小时级)",
    3: "长档 (约 12 小时级)",
    4: "很长档 (约 48 小时 / 2 天级)",
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
