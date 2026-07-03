"""get_no_side 토큰/가격 index 매핑 검증.

Hope Crusher의 생명선: outcomePrices[0]=YES, [1]=NO, clobTokenIds 동일 인덱스.
여기가 뒤집히면 실돈으로 정반대(YES 롱샷) 베팅이 된다.
"""
from polybot.strategy.filters import get_no_side


def make_market(**overrides):
    market = {
        "outcomePrices": ["0.15", "0.85"],   # [YES, NO]
        "clobTokenIds": ["YES_TOKEN_111", "NO_TOKEN_222"],
        "outcomes": ["Yes", "No"],
    }
    market.update(overrides)
    return market


class TestIndexMapping:
    def test_buys_no_token_index_1(self):
        side = get_no_side(make_market())
        # 매수 대상은 반드시 clobTokenIds[1] (NO 토큰)
        assert side["token_id"] == "NO_TOKEN_222"
        assert side["token_index"] == 1
        # 백필용 YES 토큰은 index 0
        assert side["yes_token_id"] == "YES_TOKEN_111"

    def test_price_mapping_yes_0_no_1(self):
        side = get_no_side(make_market())
        assert side["yes_price"] == 0.15   # outcomePrices[0]
        assert side["no_price"] == 0.85    # outcomePrices[1]

    def test_outcome_label_is_no(self):
        side = get_no_side(make_market())
        assert side["outcome"] == "No"


class TestMalformedInput:
    def test_unparsed_json_string_token_ids_rejected(self):
        # Gamma JSON 파싱 실패 시 str로 남는다 - len(str)>=2로 뚫리면 안 된다
        market = make_market(clobTokenIds='["YES_TOKEN_111", "NO_TOKEN_222"]')
        assert get_no_side(market) == {}

    def test_unparsed_json_string_prices_rejected(self):
        market = make_market(outcomePrices='["0.15", "0.85"]')
        assert get_no_side(market) == {}

    def test_short_lists_rejected(self):
        assert get_no_side(make_market(outcomePrices=["0.15"])) == {}
        assert get_no_side(make_market(clobTokenIds=["ONLY_ONE"])) == {}

    def test_non_numeric_price_rejected(self):
        assert get_no_side(make_market(outcomePrices=["abc", "0.85"])) == {}

    def test_missing_fields_rejected(self):
        assert get_no_side({}) == {}
