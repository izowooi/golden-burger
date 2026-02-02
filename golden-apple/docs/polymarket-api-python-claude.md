# Polymarket API Python 개발자 종합 가이드

Polymarket은 블록체인 기반 예측 시장 플랫폼으로, **하이브리드 분산형 아키텍처**를 통해 오프체인 매칭과 온체인 정산을 결합합니다. 이 가이드는 Gamma API(시장 데이터), CLOB API(거래), WebSocket(실시간 스트리밍), Data API(사용자 데이터) 전체를 다루며, 실제 운영 환경에서 사용 가능한 Python 코드 예제를 포함합니다. 모든 거래는 Polygon 네트워크에서 USDC로 이루어지며, 주문은 EIP-712 서명 방식으로 비수탁형(non-custodial) 보안을 보장합니다.

## API 아키텍처 및 엔드포인트 구조

Polymarket은 용도별로 분리된 4개의 주요 API 서비스를 제공합니다. 각 API는 독립적으로 운영되며 서로 다른 Rate Limit과 인증 요구사항을 가집니다.

| 서비스 | Base URL | 용도 |
|--------|----------|------|
| **Gamma API** | `https://gamma-api.polymarket.com` | 시장 메타데이터, 검색, 카테고리 |
| **CLOB API** | `https://clob.polymarket.com` | 주문 생성/취소, 호가창, 거래 |
| **Data API** | `https://data-api.polymarket.com` | 포지션, 거래 이력, 포트폴리오 |
| **WebSocket** | `wss://ws-subscriptions-clob.polymarket.com` | 실시간 호가/주문 업데이트 |

**핵심 데이터 구조**는 계층적입니다: Event(이벤트) → Market(시장) → Token(토큰). 예를 들어 "2025년 Fed 금리 결정" 이벤트는 여러 개의 시장(1월 인하, 3월 인하 등)을 포함하고, 각 시장은 Yes/No 두 개의 ERC-1155 토큰 ID를 가집니다. `conditionId`는 CLOB 거래용 식별자이고, `clobTokenIds`는 실제 주문에 사용되는 토큰 ID 배열입니다.

---

## 환경 설정 및 인증

### Python 환경 및 의존성 설치

```bash
# Python 3.9 이상 필요 (공식 지원: 3.9.10+)
pip install py-clob-client python-dotenv requests websocket-client
```

**설치되는 핵심 패키지**: `eth-account>=0.13.0`, `eth-utils>=4.1.1`, `poly_eip712_structs`, `py-order-utils`

### Private Key 획득 및 환경 변수 설정

Polymarket 계정에서 Private Key를 내보내려면 로그인 후 **Cash → ⋮(더보기) → Export Private Key**를 클릭합니다. 이메일 지갑 사용자는 [reveal.magic.link/polymarket](https://reveal.magic.link/polymarket)에서도 확인 가능합니다.

```python
# keys.env 파일 생성 (반드시 .gitignore에 추가)
# 0x 접두사 제거 후 저장
PK="your_private_key_without_0x_prefix"
FUNDER_ADDRESS="your_polymarket_proxy_wallet_address"

# API credentials (최초 생성 후 저장)
CLOB_API_KEY=""
CLOB_SECRET=""
CLOB_PASSPHRASE=""
```

### L1/L2 인증 체계와 Client 초기화

Polymarket은 **2단계 인증 체계**를 사용합니다. L1은 Private Key 서명으로 API credential을 생성하고, L2는 생성된 API Key/Secret/Passphrase로 거래 요청을 인증합니다.

```python
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv('keys.env')

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon Mainnet

# Signature Type 설명:
# 0 = EOA (MetaMask, 하드웨어 지갑 등 직접 서명)
# 1 = Proxy/Magic (이메일 지갑, 위임 서명)
# 2 = Browser Proxy (Gnosis Safe 등 브라우저 지갑 프록시)

def create_authenticated_client():
    """
    인증된 ClobClient 인스턴스 생성
    signature_type과 funder는 지갑 유형에 맞게 설정
    """
    private_key = os.getenv("PK")
    funder_address = os.getenv("FUNDER_ADDRESS")
    
    if not private_key:
        raise ValueError("환경 변수 PK가 설정되지 않았습니다")
    
    # 이메일/Magic 지갑 사용자용 설정
    client = ClobClient(
        host=HOST,
        key=private_key,
        chain_id=CHAIN_ID,
        signature_type=1,  # 이메일 지갑
        funder=funder_address  # 자금이 있는 프록시 지갑 주소
    )
    
    # API credentials 생성 또는 파생 (deterministic - 동일 키에서 항상 같은 결과)
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    
    print(f"API Key: {api_creds.api_key}")
    print(f"Secret: {api_creds.api_secret[:20]}...")  # 보안을 위해 일부만 출력
    
    return client

# Read-only 클라이언트 (인증 불필요)
def create_readonly_client():
    """공개 데이터 조회용 클라이언트 (인증 없이 사용)"""
    return ClobClient(HOST)
```

---

## Gamma API: 시장 데이터 조회

Gamma API는 **인증 없이** 시장 메타데이터, 이벤트, 카테고리를 조회합니다. 응답의 `outcomePrices`, `clobTokenIds`, `outcomes` 필드는 **JSON 문자열로 반환**되므로 파싱이 필요합니다.

### 주요 엔드포인트

| 엔드포인트 | 설명 | Rate Limit |
|-----------|------|-----------|
| `GET /events` | 이벤트 목록 (시장 그룹) | 100 req/10s |
| `GET /markets` | 개별 시장 목록 | 125 req/10s |
| `GET /tags` | 카테고리/태그 목록 | 750 req/10s |
| `GET /search` | 시장 검색 | 300 req/10s |

### 시장 데이터 조회 클래스

```python
import requests
import json
from typing import List, Dict, Optional

class GammaMarketClient:
    """
    Polymarket Gamma API 클라이언트
    시장 메타데이터, 이벤트, 검색 기능 제공
    """
    
    def __init__(self):
        self.base_url = "https://gamma-api.polymarket.com"
        self.session = requests.Session()
        # Connection pooling으로 성능 최적화
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "PolymarketPythonClient/1.0"
        })
    
    def _parse_market(self, market: Dict) -> Dict:
        """
        시장 데이터의 JSON 문자열 필드 파싱
        outcomePrices, clobTokenIds, outcomes는 문자열로 반환됨
        """
        for field in ['outcomePrices', 'clobTokenIds', 'outcomes']:
            if field in market and isinstance(market[field], str):
                try:
                    market[field] = json.loads(market[field])
                except json.JSONDecodeError:
                    pass
        return market
    
    def get_active_events(
        self, 
        limit: int = 50, 
        offset: int = 0,
        tag_id: Optional[int] = None,
        order: str = "volume",
        ascending: bool = False
    ) -> List[Dict]:
        """
        활성 이벤트 목록 조회
        
        Parameters:
            limit: 페이지당 결과 수 (기본 50)
            offset: 시작 위치
            tag_id: 특정 카테고리 필터 (Crypto=21 등)
            order: 정렬 기준 (id, volume, liquidity, startDate)
            ascending: 오름차순 여부
        """
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower()
        }
        if tag_id:
            params["tag_id"] = tag_id
            
        response = self.session.get(f"{self.base_url}/events", params=params)
        response.raise_for_status()
        return response.json()
    
    def get_market_by_condition_id(self, condition_id: str) -> Optional[Dict]:
        """
        conditionId로 특정 시장 조회 (CLOB 거래용 식별자)
        """
        params = {"condition_ids": condition_id, "limit": 1}
        response = self.session.get(f"{self.base_url}/markets", params=params)
        
        if response.status_code == 200:
            markets = response.json()
            return self._parse_market(markets[0]) if markets else None
        return None
    
    def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """
        URL slug으로 시장 조회 (예: "will-bitcoin-reach-100k")
        """
        response = self.session.get(f"{self.base_url}/markets/slug/{slug}")
        
        if response.status_code == 200:
            return self._parse_market(response.json())
        return None
    
    def search_markets(self, query: str) -> Dict:
        """
        시장, 이벤트, 프로필 검색
        """
        response = self.session.get(
            f"{self.base_url}/search",
            params={"q": query}
        )
        return response.json() if response.status_code == 200 else {}
    
    def get_all_tradable_markets(self) -> List[Dict]:
        """
        CLOB에서 거래 가능한 모든 활성 시장 조회 (페이지네이션 자동 처리)
        """
        all_markets = []
        offset = 0
        limit = 100
        
        while True:
            params = {
                "closed": "false",
                "enableOrderBook": "true",  # CLOB 거래 가능한 시장만
                "limit": limit,
                "offset": offset
            }
            response = self.session.get(f"{self.base_url}/markets", params=params)
            
            if response.status_code != 200:
                break
                
            markets = response.json()
            if not markets:
                break
            
            all_markets.extend([self._parse_market(m) for m in markets])
            offset += limit
            
            # 안전 제한
            if offset >= 10000:
                break
        
        return all_markets
    
    def get_market_probability(self, market: Dict) -> Dict[str, float]:
        """
        시장 가격에서 확률 추출
        가격 0.65 = 65% 확률
        """
        market = self._parse_market(market)
        outcomes = market.get('outcomes', [])
        prices = market.get('outcomePrices', [])
        
        probabilities = {}
        for i, outcome in enumerate(outcomes):
            if i < len(prices):
                prob = float(prices[i]) * 100
                probabilities[outcome] = round(prob, 2)
        
        return probabilities


# 사용 예제
gamma = GammaMarketClient()

# 거래량 기준 상위 이벤트 조회
events = gamma.get_active_events(limit=5, order="volume")
for event in events:
    print(f"이벤트: {event['title']}")
    print(f"  거래량: ${event.get('volume', 0):,.0f}")
    print(f"  시장 수: {len(event.get('markets', []))}")

# 특정 시장 검색
results = gamma.search_markets("Bitcoin")
print(f"검색 결과: {len(results.get('markets', []))}개 시장")
```

---

## CLOB API: 주문 생성 및 거래

CLOB(Central Limit Order Book)은 **하이브리드 분산형** 구조입니다. 주문 매칭은 오프체인에서 처리하여 속도를 높이고, 정산은 Polygon 온체인에서 비수탁형으로 실행됩니다.

### 주요 엔드포인트

| 엔드포인트 | 메서드 | 인증 | 설명 | Rate Limit |
|-----------|--------|------|------|-----------|
| `/book` | GET | 불필요 | 호가창 조회 | 200 req/10s |
| `/price` | GET | 불필요 | 최우선 호가 | 200 req/10s |
| `/midpoint` | GET | 불필요 | 중간가 | 200 req/10s |
| `/order` | POST | L2 | 주문 생성 | 2400 burst / 24000 sustained |
| `/order/{id}` | DELETE | L2 | 주문 취소 | 2400 burst / 24000 sustained |
| `/orders` | GET | L2 | 미체결 주문 조회 | - |
| `/trades` | GET | L2 | 체결 내역 | 75 req/10s |

### 주문 유형

| Type | 설명 |
|------|------|
| **GTC** | Good-Til-Cancelled: 취소 전까지 유효 |
| **GTD** | Good-Til-Date: 지정 시간까지 유효 (최소 60초) |
| **FOK** | Fill-Or-Kill: 즉시 전량 체결 또는 전량 취소 |
| **FAK** | Fill-And-Kill (IOC): 즉시 체결 가능한 만큼만 실행, 나머지 취소 |

### 주문 생성 및 관리 예제

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs, 
    MarketOrderArgs, 
    OrderType,
    BookParams,
    OpenOrderParams,
    TradeParams
)
from py_clob_client.order_builder.constants import BUY, SELL
import os
from dotenv import load_dotenv

load_dotenv('keys.env')

class PolymarketTrader:
    """
    Polymarket CLOB 거래 클래스
    주문 생성, 취소, 조회 기능 제공
    """
    
    def __init__(self):
        self.client = self._initialize_client()
    
    def _initialize_client(self) -> ClobClient:
        """인증된 클라이언트 초기화"""
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PK"),
            chain_id=137,
            signature_type=1,  # 이메일 지갑 (0=EOA, 2=브라우저 프록시)
            funder=os.getenv("FUNDER_ADDRESS")
        )
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    
    # ===== 시장 데이터 조회 (인증 불필요) =====
    
    def get_orderbook(self, token_id: str) -> dict:
        """
        특정 토큰의 호가창 조회
        
        Returns:
            {
                "market": "0x...",
                "asset_id": "...",
                "bids": [{"price": "0.50", "size": "100"}],
                "asks": [{"price": "0.52", "size": "80"}],
                "min_order_size": "5",
                "tick_size": "0.01"
            }
        """
        return self.client.get_order_book(token_id)
    
    def get_best_price(self, token_id: str, side: str = "BUY") -> float:
        """최우선 호가 조회 (side: "BUY" 또는 "SELL")"""
        return float(self.client.get_price(token_id, side=side))
    
    def get_midpoint(self, token_id: str) -> float:
        """중간가 조회 (bid-ask 평균)"""
        return float(self.client.get_midpoint(token_id))
    
    def get_spread(self, token_id: str) -> float:
        """스프레드 조회 (ask - bid)"""
        return float(self.client.get_spread(token_id))
    
    # ===== Limit Order (지정가 주문) =====
    
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC"
    ) -> dict:
        """
        지정가 주문 생성
        
        Parameters:
            token_id: clobTokenIds에서 가져온 토큰 ID
            price: 주문 가격 (0.01 ~ 0.99)
            size: 주문 수량 (최소 5)
            side: "BUY" 또는 "SELL"
            order_type: "GTC", "GTD", "FOK", "FAK"
        
        Returns:
            {"success": true, "orderId": "0x...", "orderHashes": ["0x..."]}
        """
        order_side = BUY if side.upper() == "BUY" else SELL
        ot = getattr(OrderType, order_type.upper())
        
        # OrderArgs 생성
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=order_side
        )
        
        # 주문 서명 및 전송
        signed_order = self.client.create_order(order_args)
        response = self.client.post_order(signed_order, ot)
        
        return response
    
    # ===== Market Order (시장가 주문) =====
    
    def place_market_order(
        self,
        token_id: str,
        amount: float,
        side: str
    ) -> dict:
        """
        시장가 주문 생성 (FOK - Fill-Or-Kill)
        
        Parameters:
            token_id: 토큰 ID
            amount: 사용할 USDC 금액 (BUY) 또는 매도할 수량 (SELL)
            side: "BUY" 또는 "SELL"
        """
        order_side = BUY if side.upper() == "BUY" else SELL
        
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=order_side,
            order_type=OrderType.FOK
        )
        
        signed_order = self.client.create_market_order(market_order)
        response = self.client.post_order(signed_order, OrderType.FOK)
        
        return response
    
    # ===== 주문 관리 =====
    
    def get_open_orders(self, market: str = None) -> list:
        """미체결 주문 목록 조회"""
        params = OpenOrderParams(market=market) if market else OpenOrderParams()
        return self.client.get_orders(params)
    
    def cancel_order(self, order_id: str) -> dict:
        """단일 주문 취소"""
        return self.client.cancel(order_id=order_id)
    
    def cancel_all_orders(self) -> dict:
        """모든 미체결 주문 취소"""
        return self.client.cancel_all()
    
    def cancel_market_orders(self, market: str) -> dict:
        """특정 시장의 모든 주문 취소"""
        return self.client.cancel_market_orders(market=market)
    
    # ===== 거래 내역 =====
    
    def get_trades(self, market: str = None) -> list:
        """
        체결된 거래 내역 조회
        
        Returns:
            [{"id": "...", "price": "0.55", "size": "100", 
              "side": "BUY", "status": "CONFIRMED", ...}]
        """
        params = TradeParams(market=market) if market else TradeParams()
        return self.client.get_trades(params)


# 사용 예제
trader = PolymarketTrader()

# 호가창 조회
token_id = "109681959945973300464568698402968596289258214226684818748321941747028805721376"
orderbook = trader.get_orderbook(token_id)
print(f"최우선 매수호가: {orderbook['bids'][0] if orderbook['bids'] else 'N/A'}")
print(f"최우선 매도호가: {orderbook['asks'][0] if orderbook['asks'] else 'N/A'}")

# 지정가 매수 주문 (50센트에 100주)
# result = trader.place_limit_order(
#     token_id=token_id,
#     price=0.50,
#     size=100.0,
#     side="BUY",
#     order_type="GTC"
# )
# print(f"주문 결과: {result}")

# 미체결 주문 조회
open_orders = trader.get_open_orders()
print(f"미체결 주문 수: {len(open_orders)}")
```

---

## WebSocket API: 실시간 데이터 스트리밍

WebSocket은 **두 개의 채널**을 제공합니다. Market 채널은 공개 호가 데이터, User 채널은 인증된 주문/체결 업데이트입니다. **10초마다 PING** 메시지를 보내야 연결이 유지되며, **연결당 최대 500개 토큰**까지 구독 가능합니다.

### 이벤트 타입

| 채널 | 이벤트 | 설명 |
|------|--------|------|
| Market | `book` | 전체 호가창 스냅샷 |
| Market | `price_change` | 호가 변경 (신규 주문/취소) |
| Market | `last_trade_price` | 체결가 업데이트 |
| Market | `tick_size_change` | Tick size 변경 (가격 극단값) |
| User | `order` | 주문 상태 (PLACEMENT, UPDATE, CANCELLATION) |
| User | `trade` | 체결 상태 (MATCHED, MINED, CONFIRMED, FAILED) |

### Production-Ready WebSocket 클라이언트

```python
from websocket import WebSocketApp
import json
import time
import threading
from typing import Callable, Optional, List, Dict
from dataclasses import dataclass


@dataclass
class WebSocketConfig:
    """WebSocket 설정"""
    reconnect_attempts: int = 5
    reconnect_delay: float = 5.0
    ping_interval: int = 10


class PolymarketWebSocket:
    """
    Polymarket WebSocket 클라이언트
    자동 재연결, PING 유지, 이벤트 핸들링 지원
    """
    
    BASE_URL = "wss://ws-subscriptions-clob.polymarket.com"
    
    def __init__(
        self,
        channel: str,  # "market" 또는 "user"
        on_book: Optional[Callable] = None,
        on_price_change: Optional[Callable] = None,
        on_trade: Optional[Callable] = None,
        on_order: Optional[Callable] = None,
        config: WebSocketConfig = None
    ):
        self.channel = channel
        self.config = config or WebSocketConfig()
        
        # 이벤트 핸들러
        self.handlers = {
            "book": on_book,
            "price_change": on_price_change,
            "last_trade_price": on_trade,
            "trade": on_trade,
            "order": on_order
        }
        
        self.ws: Optional[WebSocketApp] = None
        self.running = False
        self.attempt_count = 0
        self._subscriptions = {"asset_ids": [], "condition_ids": [], "auth": None}
    
    def _get_url(self) -> str:
        return f"{self.BASE_URL}/ws/{self.channel}"
    
    def subscribe_market(self, asset_ids: List[str]):
        """Market 채널 구독 (공개 - 인증 불필요)"""
        self._subscriptions["asset_ids"] = asset_ids
    
    def subscribe_user(self, condition_ids: List[str], auth: Dict):
        """
        User 채널 구독 (인증 필요)
        
        auth = {
            "apiKey": "...",
            "secret": "...",
            "passphrase": "..."
        }
        """
        self._subscriptions["condition_ids"] = condition_ids
        self._subscriptions["auth"] = auth
    
    def _on_open(self, ws):
        """연결 성공 시 구독 메시지 전송"""
        print(f"[{self.channel.upper()}] WebSocket 연결 성공")
        self.attempt_count = 0
        self.running = True
        
        # 채널별 구독 메시지 구성
        if self.channel == "market":
            subscription = {
                "assets_ids": self._subscriptions["asset_ids"],
                "type": "market"
            }
        elif self.channel == "user":
            if not self._subscriptions["auth"]:
                print("[ERROR] User 채널은 인증이 필요합니다")
                return
            subscription = {
                "markets": self._subscriptions["condition_ids"],
                "type": "user",
                "auth": self._subscriptions["auth"]
            }
        else:
            return
        
        ws.send(json.dumps(subscription))
        print(f"[{self.channel.upper()}] 구독 요청 전송 완료")
        
        # PING 스레드 시작 (10초 간격)
        threading.Thread(
            target=self._ping_loop, 
            args=(ws,), 
            daemon=True
        ).start()
    
    def _on_message(self, ws, message: str):
        """메시지 수신 및 핸들러 호출"""
        # PONG 응답 무시
        if message == "PONG":
            return
        
        try:
            data = json.loads(message)
            event_type = data.get("event_type", "unknown")
            
            # 등록된 핸들러 호출
            handler = self.handlers.get(event_type)
            if handler:
                handler(data)
            else:
                self._default_handler(event_type, data)
                
        except json.JSONDecodeError:
            print(f"[{self.channel.upper()}] JSON 파싱 실패: {message[:100]}")
    
    def _default_handler(self, event_type: str, data: Dict):
        """기본 이벤트 로깅"""
        if event_type == "book":
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            best_bid = bids[0]["price"] if bids else "N/A"
            best_ask = asks[0]["price"] if asks else "N/A"
            print(f"[BOOK] Bid: {best_bid} | Ask: {best_ask}")
        
        elif event_type == "price_change":
            for change in data.get("price_changes", []):
                print(f"[PRICE] {change.get('side')} @ {change.get('price')}")
        
        elif event_type == "last_trade_price":
            print(f"[TRADE] {data.get('side')} {data.get('size')} @ {data.get('price')}")
        
        elif event_type in ("trade", "order"):
            print(f"[{event_type.upper()}] Status: {data.get('status', data.get('type'))}")
    
    def _on_error(self, ws, error):
        print(f"[{self.channel.upper()}] 오류: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        print(f"[{self.channel.upper()}] 연결 종료: {close_status_code}")
        self.running = False
        
        # 자동 재연결 (exponential backoff)
        if self.attempt_count < self.config.reconnect_attempts:
            self.attempt_count += 1
            delay = self.config.reconnect_delay * (2 ** (self.attempt_count - 1))
            print(f"[{self.channel.upper()}] {delay:.1f}초 후 재연결 시도 ({self.attempt_count}회차)")
            time.sleep(delay)
            self.connect()
    
    def _ping_loop(self, ws):
        """연결 유지를 위한 PING 전송 (10초 간격)"""
        while self.running:
            try:
                ws.send("PING")
                time.sleep(self.config.ping_interval)
            except Exception as e:
                print(f"[PING] 오류: {e}")
                break
    
    def add_assets(self, asset_ids: List[str]):
        """실행 중 추가 토큰 구독 (Market 채널)"""
        if self.ws and self.channel == "market":
            msg = {"assets_ids": asset_ids, "operation": "subscribe"}
            self.ws.send(json.dumps(msg))
            self._subscriptions["asset_ids"].extend(asset_ids)
    
    def connect(self):
        """WebSocket 연결 시작 (블로킹)"""
        self.ws = WebSocketApp(
            self._get_url(),
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self.ws.run_forever()
    
    def disconnect(self):
        """연결 종료"""
        self.running = False
        if self.ws:
            self.ws.close()


# 사용 예제: 실시간 호가 추적
def handle_book_update(data):
    """호가창 업데이트 핸들러"""
    bids = data.get("bids", [])[:3]  # 상위 3개
    asks = data.get("asks", [])[:3]
    
    print(f"\n=== {data.get('asset_id', '')[:20]}... ===")
    print("매수호가:")
    for bid in bids:
        print(f"  {bid['price']} x {bid['size']}")
    print("매도호가:")
    for ask in asks:
        print(f"  {ask['price']} x {ask['size']}")


def start_market_stream(token_ids: List[str]):
    """Market 채널 스트리밍 시작"""
    ws = PolymarketWebSocket(
        channel="market",
        on_book=handle_book_update
    )
    ws.subscribe_market(token_ids)
    ws.connect()  # 블로킹


# User 채널 예제 (인증 필요)
def start_user_stream(condition_ids: List[str], api_creds):
    """User 채널 스트리밍 시작"""
    auth = {
        "apiKey": api_creds.api_key,
        "secret": api_creds.api_secret,
        "passphrase": api_creds.api_passphrase
    }
    
    ws = PolymarketWebSocket(channel="user")
    ws.subscribe_user(condition_ids, auth)
    ws.connect()
```

---

## Data API: 포지션 및 활동 내역

Data API는 사용자별 포지션, 거래 이력, 포트폴리오 데이터를 제공합니다.

```python
import requests

class DataAPIClient:
    """Polymarket Data API 클라이언트"""
    
    def __init__(self):
        self.base_url = "https://data-api.polymarket.com"
    
    def get_positions(self, address: str) -> list:
        """
        특정 주소의 포지션 조회
        
        Returns:
            [{"conditionId": "...", "outcome": "Yes", "size": 100, ...}]
        """
        response = requests.get(f"{self.base_url}/positions", params={"address": address})
        return response.json() if response.status_code == 200 else []
    
    def get_activity(
        self, 
        address: str, 
        limit: int = 50,
        condition_id: str = None
    ) -> list:
        """
        사용자 활동 내역 (거래, 입출금 등)
        
        Returns:
            [{"type": "TRADE", "timestamp": 123, "price": 0.55, ...}]
        """
        params = {"address": address, "limit": limit}
        if condition_id:
            params["market"] = condition_id
            
        response = requests.get(f"{self.base_url}/activity", params=params)
        return response.json() if response.status_code == 200 else []
    
    def get_trades_by_address(self, address: str, limit: int = 100) -> list:
        """주소별 체결 내역"""
        response = requests.get(
            f"{self.base_url}/trades",
            params={"maker_address": address, "limit": limit}
        )
        return response.json() if response.status_code == 200 else []


# 사용 예제
data_api = DataAPIClient()
my_address = "0x..."
positions = data_api.get_positions(my_address)
print(f"보유 포지션 수: {len(positions)}")
```

---

## 베스트 프랙티스 및 에러 핸들링

### Rate Limiting 처리

```python
import time
from functools import wraps
from typing import Callable

def rate_limit_handler(max_retries: int = 5, base_delay: float = 2.0):
    """
    Rate limit 및 일시적 오류 처리 데코레이터
    Exponential backoff with jitter 적용
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import random
            
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                    
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response else 0
                    
                    if status_code == 429:  # Rate Limit
                        retry_after = int(e.response.headers.get("Retry-After", base_delay))
                        jitter = random.uniform(0, 1)
                        wait_time = retry_after + jitter
                        print(f"[Rate Limit] {wait_time:.1f}초 대기 후 재시도...")
                        time.sleep(wait_time)
                        
                    elif status_code in (500, 502, 503, 504):  # Server Error
                        wait_time = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"[Server Error {status_code}] {wait_time:.1f}초 후 재시도...")
                        time.sleep(wait_time)
                        
                    else:
                        raise  # 다른 HTTP 오류는 즉시 발생
                        
                except requests.exceptions.ConnectionError:
                    wait_time = base_delay * (2 ** attempt)
                    print(f"[Connection Error] {wait_time:.1f}초 후 재시도...")
                    time.sleep(wait_time)
            
            raise Exception(f"최대 재시도 횟수({max_retries}) 초과")
        
        return wrapper
    return decorator


# 사용 예제
@rate_limit_handler(max_retries=5)
def fetch_with_retry(url: str):
    response = requests.get(url)
    response.raise_for_status()
    return response.json()
```

### 일반적인 오류 및 해결책

| 오류 | 원인 | 해결책 |
|------|------|--------|
| `INVALID_ORDER_NOT_ENOUGH_BALANCE` | 잔액 부족 | 지갑 잔액 확인, allowance 설정 |
| `INVALID_ORDER_MIN_TICK_SIZE` | 가격 정밀도 오류 | tick_size (보통 0.01) 확인 |
| `INVALID_ORDER_MIN_SIZE` | 최소 주문 수량 미달 | 최소 5 이상 |
| `FOK_ORDER_NOT_FILLED_ERROR` | FOK 전량 체결 실패 | 유동성 확인, GTC로 변경 |
| `Signature verification failed` | 서명 불일치 | signature_type 확인 (0/1/2) |
| `Funder address mismatch` | funder 주소 오류 | Polymarket 프로필 주소 확인 |

### Token Allowance 설정 (EOA 지갑)

MetaMask 등 EOA 지갑 사용자는 거래 전 **토큰 승인**이 필요합니다. 이메일/Magic 지갑은 자동 설정됩니다.

```python
# 승인이 필요한 컨트랙트 주소 (Polygon Mainnet)
USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# web3.py를 사용한 승인 (예시)
# 실제 구현 시 gas 비용 발생
```

### 보안 체크리스트

- ✅ Private Key는 절대 코드에 하드코딩하지 않기
- ✅ `.env` 파일을 `.gitignore`에 추가
- ✅ API credentials 주기적 갱신 고려
- ✅ 프로덕션에서는 AWS Secrets Manager, HashiCorp Vault 등 사용
- ✅ 모든 API 활동에 대한 로깅 구현
- ✅ 테스트 시 소액으로 시작

---

## 실전 예제: 포트폴리오 모니터링 봇

```python
import time
from datetime import datetime
from typing import Dict, List

class PortfolioMonitor:
    """
    실시간 포트폴리오 모니터링
    Gamma API + CLOB + WebSocket 통합 예제
    """
    
    def __init__(self, trader: PolymarketTrader, gamma: GammaMarketClient):
        self.trader = trader
        self.gamma = gamma
        self.positions: Dict[str, Dict] = {}
    
    def load_positions(self, condition_ids: List[str]):
        """포지션별 시장 정보 로드"""
        for cid in condition_ids:
            market = self.gamma.get_market_by_condition_id(cid)
            if market:
                token_ids = market.get('clobTokenIds', [])
                self.positions[cid] = {
                    "market": market,
                    "question": market.get('question'),
                    "token_yes": token_ids[0] if len(token_ids) > 0 else None,
                    "token_no": token_ids[1] if len(token_ids) > 1 else None
                }
    
    def get_current_prices(self) -> Dict[str, Dict]:
        """모든 포지션의 현재가 조회"""
        prices = {}
        
        for cid, pos in self.positions.items():
            token_yes = pos.get('token_yes')
            if token_yes:
                try:
                    mid = self.trader.get_midpoint(token_yes)
                    spread = self.trader.get_spread(token_yes)
                    prices[cid] = {
                        "question": pos['question'][:50],
                        "yes_price": mid,
                        "spread": spread,
                        "probability": f"{mid * 100:.1f}%"
                    }
                except Exception as e:
                    prices[cid] = {"error": str(e)}
        
        return prices
    
    def monitor_loop(self, interval: int = 30):
        """주기적 모니터링 루프"""
        print(f"포트폴리오 모니터링 시작 ({len(self.positions)}개 시장)")
        print("=" * 60)
        
        while True:
            try:
                prices = self.get_current_prices()
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                print(f"\n[{timestamp}] 포지션 현황:")
                for cid, data in prices.items():
                    if "error" not in data:
                        print(f"  {data['question']}...")
                        print(f"    Yes: {data['probability']} | Spread: {data['spread']:.2%}")
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                print("\n모니터링 종료")
                break
            except Exception as e:
                print(f"오류 발생: {e}")
                time.sleep(5)


# 실행 예제
# gamma = GammaMarketClient()
# trader = PolymarketTrader()
# monitor = PortfolioMonitor(trader, gamma)
# 
# # 모니터링할 시장 ID 설정
# my_condition_ids = [
#     "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af"
# ]
# monitor.load_positions(my_condition_ids)
# monitor.monitor_loop(interval=60)
```

---

## 트러블슈팅 빠른 참조

**"Private key not found"**
→ `keys.env` 파일에 `PK` 변수 설정, `0x` 접두사 제거

**WebSocket 연결 끊김**
→ 10초마다 PING 전송 확인, 자동 재연결 로직 구현

**주문 실패: "signature verification"**
→ `signature_type` 확인 (0=EOA, 1=이메일지갑, 2=브라우저프록시)

**"funder address mismatch"**
→ polymarket.com/settings에서 프록시 지갑 주소 확인

**429 Too Many Requests**
→ Rate limit 초과, exponential backoff 구현

이 가이드의 코드는 Polymarket 공식 문서(docs.polymarket.com)와 py-clob-client 라이브러리를 기반으로 작성되었습니다. 실제 거래 전 소액으로 테스트하고, ToS(이용약관)에서 지역 제한 사항을 반드시 확인하세요.