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


def get_high_probability_outcome(market: Dict) -> Dict:
    """Extract the high-probability outcome from market.

    Args:
        market: Market dictionary with outcomePrices and clobTokenIds

    Returns:
        Dictionary with:
        - outcome: "Yes" or "No"
        - probability: float (0.0-1.0)
        - token_id: string
        - token_index: 0 or 1
    """
    outcome_prices = market.get("outcomePrices", [])
    token_ids = market.get("clobTokenIds", [])
    outcomes = market.get("outcomes", ["Yes", "No"])

    if not outcome_prices or len(outcome_prices) < 2:
        return {}

    yes_prob = float(outcome_prices[0])
    no_prob = float(outcome_prices[1])

    # Determine which outcome has higher probability
    if yes_prob >= no_prob:
        return {
            "outcome": outcomes[0] if outcomes else "Yes",
            "probability": yes_prob,
            "token_id": token_ids[0] if token_ids else None,
            "token_index": 0,
        }
    else:
        return {
            "outcome": outcomes[1] if len(outcomes) > 1 else "No",
            "probability": no_prob,
            "token_id": token_ids[1] if len(token_ids) > 1 else None,
            "token_index": 1,
        }


def is_valid_buy_candidate(
    probability: float,
    buy_threshold: float,
    sell_threshold: float,
) -> bool:
    """Check if probability is valid for buying.

    Valid range: buy_threshold <= probability <= sell_threshold

    Note: 변경됨 - sell_threshold까지 포함 (기존: < sell_threshold)
    97%에서도 진입 가능하며, 이후 이익실현/손절/데드크로스로 청산.

    Args:
        probability: Current probability
        buy_threshold: Minimum probability to buy (e.g., 0.85)
        sell_threshold: Maximum probability to buy (e.g., 0.97)

    Returns:
        True if probability is in valid buy range
    """
    return buy_threshold <= probability <= sell_threshold


def should_sell(probability: float, sell_threshold: float) -> bool:
    """Check if current probability triggers sell condition.

    Args:
        probability: Current probability
        sell_threshold: Threshold to trigger sell (e.g., 0.90)

    Returns:
        True if should sell
    """
    return probability >= sell_threshold
