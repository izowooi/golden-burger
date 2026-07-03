"""Market filtering functions.

excluded_categories는 기본 비활성([])이다. SPORTS_KEYWORDS에 "game",
"match", "win the" 같은 일반 단어가 포함되어 비스포츠 시장까지 과차단하는
문제가 확인되었기 때문이다. 필요 시 POLYBOT_EXCLUDED_CATEGORIES env로 켠다.
"""
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


def passes_volume_filter(market: Dict, min_volume_24h: float) -> bool:
    """Check if market has sufficient 24h volume (gamma volume24hr).

    Args:
        market: Market dictionary
        min_volume_24h: Minimum required 24h volume in USD

    Returns:
        True if market has sufficient 24h volume
    """
    volume = float(market.get("volume24hr") or 0)
    return volume >= min_volume_24h
