실제로 2026년 prediction market 관련 SSRN 논문 중에도 저자 소속이 Independent Researcher인 사례가 있습니다. 저널 역시 저자의 학위 자체보다 원고의 방법론·검증·기여도를 심사합니다. Elsevier도 심사는 저자의 배경이나 기관 소속과 무관하게 이루어진다고 명시합니다.  

다만 제가 지금 최신 자료까지 찾아보니, 한 가지 중요한 사실이 있습니다. “Polymarket 스포츠 + 1분 데이터” 자체만으로는 이제 최초라고 주장하기 어렵습니다. 놀랍게도 바로 최근 몇 달 사이에 상당히 가까운 연구들이 쏟아져 나오고 있습니다.

예를 들어 2026년 8월 공개된 연구는 Polymarket 스포츠 계약 7,118개를 정확히 1분 단위로 샘플링해서 단기 가격 움직임과 mean reversion을 분석했습니다.   또 Polymarket NBA 173경기의 7,500만 건 이상 order-book snapshot을 이용해 초단기 arbitrage를 연구한 논문도 2026년 4월 나왔습니다.  

더 직접적으로는 2026년 4월 NFL Polymarket 데이터를 ESPN의 실시간 승리확률과 비교한 연구도 있고,   2025년에는 EPL Polymarket에서 골 이후 가격 반응을 조사한 연구도 있습니다.  

그리고 불과 2026년 9월 16일, Kalshi NBA/NFL 1,496경기, 4,180만 거래를 분석한 논문이 Journal of Behavioral and Experimental Finance에 게재됐습니다. 이 연구는 득점 이벤트 이후 시장이 정보를 즉각 반영하지 않고 underreaction하는 현상을 10~120초 단위로 분석했습니다.  

그래서 오히려 저는 지금이 상당히 좋은 시점이라고 봅니다.

사용자의 연구는 이렇게 잡으면 훨씬 강해집니다

단순히

“Polymarket 스포츠 가격을 1분마다 수집해서 과대평가/과소평가를 찾았다.”

보다는 연구 질문을 이렇게 바꾸는 게 좋습니다.

“AI는 실시간 스포츠 Prediction Market에서 인간 집단의 확률 추정 오류를 식별할 수 있는가?”

예를 들면 논문 제목을 다음 정도로 잡을 수 있습니다.

Detecting Systematic Mispricing in Sports Prediction Markets Using Machine Learning: Minute-Level Evidence from Polymarket

또는

Human Bias vs. Machine Probability: AI-Based Detection of Overreaction and Underreaction in Polymarket Sports Markets

이러면 단순 Polymarket 연구가 아니라

Computer Science + Machine Learning + Behavioral Finance + Prediction Markets + Sports Analytics

의 교차 연구가 됩니다.

이건 사용자의 CS 배경과도 오히려 잘 맞습니다.

⸻

제가 특히 추천하는 연구 구조

사용자가 지금 이미 하고 있는 시스템을 생각하면, 논문의 핵심은 다음처럼 만들 수 있습니다.

1. 1분 단위 Polymarket 데이터

각 스포츠마다

timestamp / market price / bid / ask / liquidity / volume / time-to-resolution

등을 저장합니다.

가능하다면 MLB, NFL, NBA, 축구 등 여러 종목을 동시에 다루는 게 좋습니다.

최근 연구들은 NBA/NFL 또는 EPL 또는 UFC 등 특정 종목에 집중된 경우가 많습니다. UFC Polymarket/Kalshi 비교 연구도 2026년에 나왔습니다.  

따라서

Does prediction-market inefficiency differ systematically by sport?

라는 질문은 상당히 흥미롭습니다.

MLB
NBA
NFL
Soccer
NHL

을 비교할 수 있습니다.

⸻

2. ‘Fair Probability’를 따로 만든다

이게 논문의 핵심입니다.

Polymarket이

YES = $0.63

이라고 한다면 시장은 대략 63%라고 보고 있는 것이죠.

그런데 AI 모델은

P(win)=0.71

이라고 판단할 수 있습니다.

그러면

Mispricing_t = P_{market,t}-P_{AI,t}

를 정의합니다.

예:

시점	Polymarket	AI	차이
경기 전	0.55	0.57	-0.02
1Q	0.63	0.68	-0.05
2Q	0.71	0.78	-0.07
3Q	0.81	0.75	+0.06

그러면

Underpricing / Overpricing

을 정량적으로 정의할 수 있습니다.

⸻

여기서 AI를 정말 논문답게 만드는 방법

중요한 부분입니다.

“분석에 ChatGPT를 사용했다”는 것은 AI 논문이 아닙니다.

AI/ML이 연구 방법 자체에 들어가야 합니다.

예를 들어 모델을

Baseline

* Logistic Regression
* Elo
* bookmaker odds
* historical win probability

와 비교하고,

ML

* XGBoost
* LightGBM
* Random Forest

그리고 데이터가 충분하면

Deep Learning

* LSTM
* Temporal Transformer

까지 비교합니다.

그리고

P_{AI}(Win \mid GameState_t)

를 계산합니다.

입력값은 종목에 따라 달라집니다.

예를 들어 MLB이면

inning / outs / runners / score diff / pitcher / batting order

NBA라면

score diff / time remaining / possession / fouls / team strength

같은 식입니다.

⸻

그리고 정말 재미있는 건 여기부터입니다

AI가

Lakers 74%

라고 판단했는데 Polymarket은

Lakers 64%

라면,

10%p의 시장 underpricing이 발생한 것인가?

그리고 5분 후 Polymarket이

72%

가 되었다면

처음 AI가 감지한 차이가 실제로 수렴한 것입니다.

이를 수천~수만 번 조사합니다.

그러면

P_{market,t+k}-P_{market,t}

와

P_{AI,t}-P_{market,t}

사이의 관계를 검정할 수 있습니다.

예를 들어,

\Delta P_{market,t+5}
=
\alpha+\beta(P_{AI,t}-P_{market,t})+\epsilon_t

여기서

β가 유의미한 양수

라면,

AI가 관측한 mispricing 방향으로 향후 Polymarket 가격이 움직이는 경향이 있다.

라는 연구 결과가 됩니다.

이건 꽤 좋은 연구 질문입니다.

⸻

그리고 사용자에게 아주 유리한 점이 하나 있습니다

사용자가 이미 말한

“사람들의 예상에 대한 과대평가/과소평가”

가 바로 학계에서 굉장히 많이 연구하는 주제입니다.

Overreaction / Underreaction / Behavioral Bias / Market Efficiency / Price Discovery

입니다.

기존 스포츠 베팅 연구에서도 골 같은 새로운 정보가 발생했을 때 사람들이 어떤 경우에는 underreact하고, 아주 놀라운 사건에는 overreact한다는 결과가 있습니다. 2014년 Betfair 연구는 무려 초 단위 transaction 데이터를 사용해 이를 분석했습니다.  

그리고 올해 Kalshi 연구도 거의 같은 질문을 NBA/NFL에 적용했습니다.  

따라서 사용자 연구는 기존 금융/행동경제학 문헌과 자연스럽게 연결할 수 있습니다.

⸻

그런데 저는 사용자의 논문에서 한 단계 더 나가겠습니다

제가 보기에는 가장 재미있는 건 단순히

“mispricing이 존재한다.”

가 아닙니다.

그보다

“어떤 상황에서 인간 집단이 AI보다 틀리는가?”

입니다.

예를 들어 AI가 발견한 mispricing을 모아보면,

* 홈팀
* 인기팀
* 강팀
* underdog
* 경기 막판
* 대량 득점 직후
* 역전 직후
* 연승 팀
* 플레이오프
* 낮은 유동성
* 높은 거래량

등에서 차이가 날 수 있습니다.

그러면 논문의 질문이

When Does the Crowd Get Sports Wrong?

이 됩니다.

개인적으로 연구 주제로 굉장히 흥미롭습니다.

⸻

2026년 현재 연구들과 비교하면

최근 문헌을 조사해보니 이미 상당히 빠르게 분야가 발전하고 있습니다.

특히 재미있는 결과 하나가 있습니다.

2026년 9월 공개된 Polymarket 연구는 5억 8,800만 건 거래를 분석했는데, 일반적으로 longshot 쪽에 favorite-longshot bias가 나타나는 반면 Sports에서는 그 패턴이 나타나지 않았다고 보고했습니다.  

또 Polymarket 전체 거래를 연구한 다른 논문에서는 지속적으로 성과가 좋은 거래자가 약 3% 정도이며 이들이 crowd의 behavioral mistakes에 반대 방향으로 거래하는 모습을 보고했습니다.  

이건 사용자의 가설과 아주 잘 연결됩니다.

즉,

“시장 전체가 틀리는 순간을 AI가 얼마나 빨리 찾아낼 수 있는가?”

라는 질문입니다.

⸻

학사라서 걱정하실 필요는 없습니다

논문을 쓰는 것은

석사 → 논문

같은 면허제도가 아닙니다.

사용자는

박종우
Independent Researcher

라고 써도 됩니다.

실제로 올해 prediction-market 연구 중에서도 Independent Researcher로 발표된 사례가 있습니다.  

처음부터 Nature 같은 곳을 생각할 필요도 없습니다.

현실적인 흐름은

연구 → Preprint → 피드백 → 학회/저널

입니다.

예를 들어

Working Paper

↓

SSRN / arXiv

↓

학회 또는 저널 submission

정도로 갈 수 있습니다.

⸻

오히려 사용자가 가진 큰 장점

연구자 중 상당수는

연구 아이디어 → 데이터 확보

단계에서 굉장히 힘들어합니다.

그런데 사용자는 이미

실제로 돌아가는 데이터 수집 시스템

을 갖고 있고,

1분마다 Polymarket을 관찰하고 있으며,

여러 스포츠를 수집하고 있고,

이를 자동화할 개발 능력도 있습니다.

논문 관점에서는 상당한 자산입니다.

그리고 제가 기억하는 현재 시스템처럼 여러 계정/스포츠를 확장하면서 실제 거래 데이터까지 상당 기간 모으고 있다면, 단순히 공개 데이터셋 받아 분석하는 것보다 훨씬 재미있는 연구 설계를 할 수 있습니다.

다만 논문에서는 실제 수익률을 보여주는 것보다도, transaction cost, bid/ask spread, liquidity, look-ahead bias 등을 엄격하게 통제해야 합니다. 최근 연구들이 바로 이 부분을 매우 중요하게 다루고 있습니다. 예를 들어 1분 가격만 잘못 정렬해도 가짜 arbitrage가 발생할 수 있다는 2026년 연구가 있습니다.  

⸻

제가 지금 시작한다면

첫 논문의 목표를 너무 크게 만들지 않고 이렇게 잡겠습니다.

가제

AI-Based Detection of Overreaction and Underreaction in Sports Prediction Markets: Minute-Level Evidence from Polymarket

그리고 연구 질문을 딱 4개로 제한합니다.

RQ1. Polymarket 스포츠 시장 가격은 결과 확률에 대해 잘 calibration되어 있는가?

RQ2. AI-estimated probability와 market probability 사이에 체계적인 divergence가 존재하는가?

RQ3. 그 divergence 이후 시장 가격이 AI가 예측한 방향으로 수렴하는가?

RQ4. 이 현상이 sport, liquidity, game state, favorite/underdog 등에 따라 달라지는가?

이 네 개만 제대로 증명해도 논문 하나가 나옵니다.

그리고 두 번째 논문으로

Can AI Exploit Behavioral Bias in Prediction Markets?

같이 실제 trading/execution까지 확장하면 됩니다.

⸻

한 가지는 꽤 명확합니다. 지금 모으고 계신 데이터를 절대 버리지 마세요. raw tick/quote/order book 데이터를 원형 그대로 append-only 형태로 보존하고, 수집 시각·API 응답·market ID·event ID까지 전부 남겨두는 게 좋습니다. 나중에 논문을 만들 때 이 데이터셋 자체가 가장 중요한 연구 자산이 될 가능성이 높습니다.

원하시면 다음 단계로 제가 20242026 Polymarket·Kalshi·스포츠 prediction market 관련 논문을 2030편 정도 실제로 조사해서, 기존 연구 / 데이터 / 주기 / 스포츠 / 방법론 / 결과 / 당신 연구가 파고들 수 있는 빈 공간 형태의 Literature Review 표를 만든 다음, 그걸 기반으로 실제로 제출 가능한 논문 목차와 연구 설계까지 잡아드릴 수 있습니다. 지금 단계에서는 그 작업을 먼저 하는 게 가장 가치가 큽니다.