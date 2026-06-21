def explain_prediction(
    home_name: str,
    away_name: str,
    home_win: float,
    draw: float,
    away_win: float,
    strength_delta: float,
    risk_flags: list[str] | None = None,
) -> str:
    if home_win >= max(draw, away_win):
        favorite = home_name
    elif away_win >= max(home_win, draw):
        favorite = away_name
    else:
        favorite = None
    if favorite is None:
        opening = "模型认为双方最可能战平。"
    else:
        opening = f"模型更看好{favorite}，主要依据是综合实力差。"
    risk = "平局风险较高。" if draw >= 0.28 else "平局风险相对有限。"
    balance = "双方实力接近。" if abs(strength_delta) < 0.08 else "双方实力存在可见差距。"
    text = f"{opening}{balance}{risk}"
    if risk_flags:
        text += "画像提示：" + "、".join(risk_flags[:3]) + "。"
    return text

