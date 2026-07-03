"""Market filtering functions."""
from typing import List, Dict

# Sports-related keywords for filtering
SPORTS_KEYWORDS = [
    # Leagues
    "nba", "nfl", "mlb", "nhl", "mls", "fifa", "uefa", "atp", "wta",
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
    "champions league", "world cup", "olympics", "super bowl",
    # Sports
    "basketball", "football", "soccer", "baseball", "hockey", "tennis",
    "golf", "boxing", "ufc", "mma", "wrestling", "cricket", "rugby",
    "f1", "formula 1", "nascar", "racing",
    # Teams (common)
    "lakers", "celtics", "warriors", "nuggets", "bulls", "knicks",
    "yankees", "dodgers", "red sox", "cubs",
    "cowboys", "patriots", "eagles", "chiefs",
    "real madrid", "barcelona", "manchester", "liverpool", "chelsea",
    # Players and terms
    "playoff", "finals", "championship", "tournament", "match", "game",
    "win the", "beat", "defeat", "score", "goal", "touchdown", "home run",
]


def is_sports_market(market: Dict, excluded_categories: List[str]) -> bool:
    """Check if market is sports-related.

    Checks:
    1. Tags (if available)
    2. Question text for sports keywords
    3. Slug for sports keywords

    주의: SPORTS_KEYWORDS는 "game", "match" 등 과차단 키워드를 포함하므로
    excluded_categories 기본값은 빈 배열(필터 완전 비활성화)이다.
    env POLYBOT_EXCLUDED_CATEGORIES로 켤 수 있다.

    Args:
        market: Market dictionary
        excluded_categories: List of category names to exclude

    Returns:
        True if market should be excluded (is sports-related)
    """
    # excluded_categories가 비어있으면 필터링 없음 (모든 시장 스캔)
    if not excluded_categories:
        return False

    # Check tags first
    tags = market.get("tags", [])
    if tags and is_sports_category(tags, excluded_categories):
        return True

    # Check question and slug for sports keywords
    question = market.get("question", "").lower()
    slug = market.get("slug", "").lower()
    text_to_check = f"{question} {slug}"

    # Check excluded categories as keywords
    for category in excluded_categories:
        if category.lower() in text_to_check:
            return True

    # Check sports keywords
    for keyword in SPORTS_KEYWORDS:
        if keyword in text_to_check:
            return True

    return False


def is_sports_category(tags: List, excluded_categories: List[str]) -> bool:
    """Check if market belongs to sports or excluded category.

    Args:
        tags: List of tag dictionaries or strings from market
        excluded_categories: List of category names to exclude

    Returns:
        True if market should be excluded (is sports/excluded)
    """
    if not tags:
        return False

    # Normalize excluded categories to lowercase for comparison
    excluded_lower = [cat.lower() for cat in excluded_categories]

    for tag in tags:
        # Handle both dict format and string format
        if isinstance(tag, dict):
            tag_slug = tag.get("slug", "").lower()
            tag_label = tag.get("label", "").lower()
            if tag_slug in excluded_lower or tag_label in excluded_lower:
                return True
        else:
            tag_str = str(tag).lower()
            if tag_str in excluded_lower:
                return True

    return False


def passes_liquidity_filter(market: Dict, min_liquidity: float) -> bool:
    """Check if market has sufficient liquidity.

    Args:
        market: Market dictionary
        min_liquidity: Minimum required liquidity in USD

    Returns:
        True if market has sufficient liquidity
    """
    liquidity = float(market.get("liquidity") or 0)
    return liquidity >= min_liquidity


def get_no_side(market: Dict) -> Dict:
    """시장에서 NO 쪽 토큰 정보를 추출 (Hope Crusher는 항상 NO 매수).

    방향이 전략에 내장되어 있다: outcomePrices[0]=YES 가격, [1]=NO 가격,
    clobTokenIds 동일 인덱스. 항상 index 1(NO)을 선택한다.

    Args:
        market: Market dictionary with outcomePrices and clobTokenIds

    Returns:
        Dictionary with:
        - outcome: "No" (outcomes[1])
        - yes_price: float (YES 가격 - 시그널 판정 기준)
        - no_price: float (NO 가격 - 매수 대상)
        - token_id: NO 토큰 ID
        - yes_token_id: YES 토큰 ID (히스토리 백필용)
        빈 dict if market data is malformed
    """
    outcome_prices = market.get("outcomePrices", [])
    token_ids = market.get("clobTokenIds", [])
    outcomes = market.get("outcomes", ["Yes", "No"])

    # JSON 파싱 실패 시 str로 남는다 - len(str)>=2로 통과해 token_ids[1]이
    # 한 글자가 되는 것을 막기 위해 list/tuple만 허용한다.
    if not isinstance(outcome_prices, (list, tuple)) or len(outcome_prices) < 2:
        return {}
    if not isinstance(token_ids, (list, tuple)) or len(token_ids) < 2:
        return {}

    try:
        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])
    except (TypeError, ValueError):
        return {}

    return {
        "outcome": outcomes[1] if len(outcomes) > 1 else "No",
        "yes_price": yes_price,
        "no_price": no_price,
        "token_id": token_ids[1],
        "yes_token_id": token_ids[0],
        "token_index": 1,
    }
