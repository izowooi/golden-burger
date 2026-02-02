# Polymarket API Usage Guide (Python Integration)

**Polymarket** is a decentralized prediction market platform. This guide explains how to integrate with Polymarket’s API using Python, covering everything from setup to placing orders. Code examples are provided in Python and are ready to copy-paste. Each section is organized with clear steps and best practices for a smooth developer experience.

## Setup and Installation

To start, install the official Polymarket Python CLOB client, which simplifies interactions with the API:

pip install py-clob-client

This requires Python 3.9 or higher[\[1\]](https://github.com/Polymarket/py-clob-client#:~:text=,env)[\[2\]](https://github.com/Polymarket/py-clob-client#:~:text=Installation). Ensure you have the following ready before coding:

* **Private Key:** The private key of your wallet that holds funds on Polymarket. This will be used for signing (L1 authentication).

* **Funder (Proxy) Address (if applicable):** If you use a proxy wallet (like an email/Magic wallet or Gnosis Safe), you’ll also need the proxy **funder address** that actually holds your funds on Polymarket[\[3\]](https://github.com/Polymarket/py-clob-client#:~:text=HOST%20%3D%20,Address%20that%20holds%20your%20funds)[\[4\]](https://github.com/Polymarket/py-clob-client#:~:text=PRIVATE_KEY%20%3D%20%22%3Cyour,Address%20that%20holds%20your%20funds). For standard Ethereum wallets (EOA like MetaMask), the funder is just your wallet’s address.

* **Environment Setup:** *Never hardcode secrets.* Store your private key (and API secret, discussed later) in environment variables or a secure vault[\[5\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=)[\[1\]](https://github.com/Polymarket/py-clob-client#:~:text=,env). For example, use a .env file or OS environment variables and load them in your script.

After installation, import the client in your Python code:

from py\_clob\_client.client import ClobClient  
import os

HOST \= "https://clob.polymarket.com"    \# Polymarket CLOB API endpoint  
CHAIN\_ID \= 137                          \# Polygon mainnet chain ID for Polymarket

Now you’re ready to connect and authenticate with the API.

## Authentication with API Key (L1 → L2)

Polymarket’s API uses a **two-level authentication system**[\[6\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=The%20CLOB%20uses%20two%20levels,public%20methods%20and%20public%20endpoints):

* **L1 (Layer 1):** Uses your wallet’s **private key** to sign a message. This proves control of the wallet on-chain and is used to generate API credentials.

* **L2 (Layer 2):** Uses an **API key** (and secret & passphrase) to authenticate your requests via HMAC signatures[\[7\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=L2%20Authentication). This is akin to traditional API key auth and is used for actual trading and user-specific data endpoints.

**Why two levels?** L1 keeps your private key local (you sign a message) and lets you obtain API keys. L2 credentials (API key, secret, passphrase) can then be used for high-frequency trading without re-signing each request[\[8\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=What%20is%20L2%3F)[\[9\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=What%20This%20Enables). Public data (like market info) needs no auth at all[\[6\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=The%20CLOB%20uses%20two%20levels,public%20methods%20and%20public%20endpoints).

### Generating API Credentials (L1)

First, create a client with L1 access by providing your private key. Then use it to generate or retrieve your API key credentials:

\# Initialize client with L1 (private key) auth  
private\_key \= os.getenv("PRIVATE\_KEY")          \# never hardcode; use env variable  
client\_l1 \= ClobClient(host=HOST, chain\_id=CHAIN\_ID, key=private\_key)

\# Generate or retrieve API credentials (L1 \-\> L2)  
api\_creds \= client\_l1.create\_or\_derive\_api\_creds()  
print(api\_creds)

When you call create\_or\_derive\_api\_creds(), the client will either fetch your existing API key or create a new one if none exists[\[10\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=%2F%2F%20Gets%20API%20key%2C%20or,createOrDeriveApiKey). The result api\_creds is a dictionary containing:

{  
  "apiKey": "\<your-api-key\>",  
  "secret": "\<base64-secret\>",  
  "passphrase": "\<passphrase\>"  
}

You'll need all three values for L2 authentication[\[11\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=%7B%20%22apiKey%22%3A%20%22550e8400,). **Store these securely** (as you would a password). With these credentials, you can initialize an L2-authenticated client.

### Using API Key Credentials (L2)

Now create a new client instance with your API credentials. You still provide the private key (for signing orders) along with the API creds:

\# Initialize client with L2 auth (API key credentials)  
client \= ClobClient(  
    host=HOST,  
    chain\_id=CHAIN\_ID,  
    key=private\_key,         \# Private key for signing (L1)  
    creds=api\_creds,         \# API key credentials for L2  
    signature\_type=0         \# 0 for standard wallets (EOA); see below  
    \# funder=\<ADDRESS\>      \# Only needed for proxy wallets; not required for EOA  
)

In this example, signature\_type=0 indicates a normal externally-owned wallet (like MetaMask or a hardware wallet)[\[12\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Signature%20Types). If you were using an email/Magic wallet or other proxy setup, you’d use signature\_type=1 and supply the funder address (the address holding your funds)[\[13\]](https://github.com/Polymarket/py-clob-client#:~:text=)[\[14\]](https://github.com/Polymarket/py-clob-client#:~:text=HOST%20%3D%20,Address%20that%20holds%20your%20funds). For most use cases, an EOA with signature\_type=0 is appropriate.

At this point, client is fully authenticated for both reading data and executing trades. (If you skip the API key step, calling L2-only methods will result in an **L2\_AUTH\_NOT\_AVAILABLE** error[\[15\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Error%3A%20L2_AUTH_NOT_AVAILABLE).)

## Retrieving Active Markets

Polymarket organizes markets under **events** (questions). Each event can have one or more binary markets (Yes/No outcomes). Before trading, you’ll want to retrieve a list of active markets to find opportunities.

Polymarket provides a **Gamma API** for market discovery (metadata, events, markets) which requires no authentication[\[16\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=etc.%29,quotes%2C%20and%20to%20submit%20or)[\[17\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=,retrieve%20nested%20data%20like%20the). We can use the Python client (in public mode) or direct API calls to fetch this data.

**Listing active markets:** To get all currently live markets, query the Gamma API for active events. For example, an HTTP request can be made to:

curl "https://gamma-api.polymarket.com/events?active=true\&closed=false\&limit=5"

This fetches events where active=true (event is ongoing) and closed=false (market not resolved)[\[18\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=List%20all%20currently%20active%20events,on%20Polymarket). **Tip:** Always filter with active=true\&closed=false to get live tradable markets[\[19\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=).

Using the Python client, you can retrieve markets without any auth:

\# Public client (no auth) for market data  
public\_client \= ClobClient(HOST)  \# no API creds or key needed for public endpoints

markets\_data \= public\_client.get\_simplified\_markets()  
print(f"Found {len(markets\_data\['data'\])} markets.")  
\# Example: print first market's title and prices  
first\_market \= markets\_data\["data"\]\[0\]  
print(first\_market\["title"\], "-", first\_market\["outcomePrices"\])

This will return a JSON structure with a list of markets. Each market entry typically includes fields like the event title, market id, outcomes, and current prices. For example, a market might look like:

{  
  "id": "789",  
  "question": "Will Bitcoin reach $100k by 2025?",  
  "outcomes": \["Yes", "No"\],  
  "outcomePrices": \["0.65", "0.35"\],  
  ...  
}

Here the outcomes "Yes" and "No" correspond to implied probabilities of 65% and 35% respectively[\[20\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Markets%20have%20,These%20prices%20represent%20implied%20probabilities)[\[21\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=%7B%20,probability). The outcomePrices array gives the current market probability for each outcome (as a string).

**Note:** Under the hood, the client’s get\_simplified\_markets() method calls Polymarket’s Gamma API (e.g., GET /markets). The Gamma API response includes helpful metadata like tags, volume, and the unique token IDs for each outcome, among other details[\[22\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=curl%20%22https%3A%2F%2Fgamma). Those token IDs (called clobTokenIds) are crucial for price queries and placing orders, as explained next.

## Fetching Market Details and Probabilities

Once you identify a market of interest (e.g., by its slug or ID), you can get detailed information including outcome tokens and current odds:

* **Market details:** Use the Gamma API to fetch a specific market by ID or slug. For example, GET /markets?slug=will-bitcoin-reach-100k-by-2025 will return the full details of that market[\[23\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Once%20you%20have%20an%20event%2C,using%20its%20ID%20or%20slug). In the response, look for clobTokenIds – an array of token IDs corresponding to each outcome. You’ll need these IDs for querying prices or placing orders[\[22\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=curl%20%22https%3A%2F%2Fgamma).

* **Current probabilities:** The simplest way to get an outcome’s current probability is from the outcomePrices in the market details (as shown above, e.g., Yes \= 0.65 meaning 65%). These update when trades occur. For a more real-time quote, Polymarket’s CLOB API allows querying the order book:

* **Best price quote:** Use the CLOB endpoint GET /price to fetch the current best bid/ask for an outcome token. For example, to get the best price to **buy** a “Yes” token, call:

* curl "https://clob.polymarket.com/price?token\_id=YOUR\_TOKEN\_ID\&side=buy"

* This returns a JSON like {"price": "0.65"} meaning the lowest ask price is 0.65[\[24\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Query%20the%20CLOB%20for%20the,current%20price%20of%20any%20token)[\[25\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Ask%20AI) (i.e. you can buy at 65% probability). If you query with side=sell, you’d get the highest bid price (what you can sell at).

* **Full order book depth:** You can fetch the entire order book for a market with GET /book?token\_id=YOUR\_TOKEN\_ID. This returns all current bids and asks with their sizes[\[26\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Get%20Orderbook%20Depth)[\[27\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=%7B%20,). For example, you might see a few best bids at 0.64, 0.63 etc., and asks at 0.65, 0.66, etc., each with available quantity.

Using the Python client, you can retrieve these as well:

token\_id \= "\<token id for outcome\>"  \# e.g., the Yes token ID from clobTokenIds  
best\_buy\_price \= public\_client.get\_price(token\_id, side="BUY")    \# Best offer to buy (as float or str)  
order\_book \= public\_client.get\_order\_book(token\_id)               \# Full order book snapshot

print(f"Best Buy Price: {best\_buy\_price}")  
print(f"Top 3 bids: {order\_book\['bids'\]\[:3\]}")  
print(f"Top 3 asks: {order\_book\['asks'\]\[:3\]}")

This example prints the best buy price (which corresponds to the Yes token’s probability) and slices of the bids/asks list. The **midpoint price** (average of best bid and ask) is also available via client.get\_midpoint(token\_id) if you need a quick implied fair price.

## Viewing User Positions and Trade History

Polymarket provides a **Data API** for user-specific data such as current positions and past trades[\[28\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=,API%20to%20retrieve%20all%20open). These endpoints typically require specifying a user’s address (and may require auth, since they return sensitive info).

**User Positions:** “Positions” are the outcome tokens you currently hold in various markets. You can query all open positions for a given wallet address via the Data API:

* **GET /positions?user=\<address\>** – Returns a list of all holdings for that user, including details like outcome, size (number of shares), average purchase price, current value, and profit/loss[\[29\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=%7B%20,123)[\[30\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=,string). This lets you see, for example, that your wallet holds 100 “Yes” shares of Market X bought at 0.60 (60%), now worth 0.65 each, with a certain P\&L.

You can call this directly using Python’s requests library:

import requests

user\_address \= "\<YOUR\_WALLET\_ADDRESS\>"  \# 0x... (the address that holds your funds)  
resp \= requests.get("https://data-api.polymarket.com/positions", params={"user": user\_address})  
positions \= resp.json()  
print(f"Open positions found: {len(positions)}")  
if positions:  
    first\_pos \= positions\[0\]  
    print("Example position:", first\_pos\["title"\], "-", first\_pos\["outcome"\],   
          f"{first\_pos\['size'\]} shares at avg price {first\_pos\['avgPrice'\]}")

The Data API can be filtered by specific markets or events using query params (see Polymarket docs for advanced usage)[\[31\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=)[\[32\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=%60), but the above will fetch all current positions.

**Trade History:** To get past trades for a user:

* **GET /trades?user=\<address\>** – returns all historical trades executed by that address[\[33\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=logs,Gamma%20and%20CLOB%20APIs%20by). Each entry includes the market, outcome, price, size, timestamp, etc., allowing you to reconstruct your trading history or PnL.

If you have an authenticated client (with L2 creds), you can retrieve your own trade history easily:

\# Using the previously authenticated \`client\`  
trades \= client.get\_trades()  \# Fetch all trades for the API key's user  
print(f"Total trades: {len(trades)}")  
if trades:  
    last\_trade \= trades\[-1\]  
    print("Last trade \-\> Market:", last\_trade\["title"\], "| Outcome:", last\_trade\["outcome"\],   
          "| Price:", last\_trade\["price"\], "| Size:", last\_trade\["size"\])

This uses the client.get\_trades() method to get trades for the user tied to the API credentials[\[34\]](https://github.com/Polymarket/py-clob-client#:~:text=last%20%3D%20client.get_last_trade_price%28%22%3Ctoken,get_trades%28%29%20print%28last%2C%20len%28trades). If you need trades for another user, you would use the Data API with that user’s address (note: that may require appropriate permissions or a public lookup if available).

## Placing Orders / Bets via the CLOB API

The core of Polymarket’s trading is the **Central Limit Order Book (CLOB)**. You can place limit orders (offers to buy or sell at a certain price) or market orders (execute immediately against the best available prices). The Polymarket Python client helps construct and submit orders with proper signing.

**Prerequisites:** Make sure you have: \- Initialized the client with L2 auth (private key \+ API creds) as shown earlier. \- Sufficient funds in your **funder** address (for EOA, your wallet) – e.g., USDC for buying shares, or outcome tokens for selling shares[\[35\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20balance). \- Approved the Polymarket Exchange contract to spend your tokens if using an EOA wallet. (The first time you trade via the UI, an approval transaction is required, or you can manually call the token contract’s approve/setApprovalForAll method[\[36\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20allowance).)

### Example: Placing a Limit Order

Let’s walk through placing a **BUY limit order** for an outcome token at a specific price. We’ll assume you have the client set up with your credentials.

from py\_clob\_client.clob\_types import OrderArgs, OrderType  
from py\_clob\_client.order\_builder.constants import BUY

\# Define order parameters  
token\_id \= "\<OUTCOME\_TOKEN\_ID\>"      \# token ID for the outcome you want to trade (Yes token, for example)  
order\_price \= 0.65                   \# price per share (e.g., 0.65 means 65% probability)  
order\_size \= 10                      \# number of shares to buy

\# Create and post a limit order in two steps:  
order\_args \= OrderArgs(token\_id=token\_id, price=order\_price, size=order\_size, side=BUY)  
signed\_order \= client.create\_order(order\_args)             \# 1\. Sign the order (offline signature)  
result \= client.post\_order(signed\_order, OrderType.GTC)    \# 2\. Submit the order (Good-'Til-Cancel)

print("Order placed\! ID:", result.get("orderID", result.get("id")))

Here we use OrderArgs to specify the order and OrderType.GTC for a Good-Til-Cancel order (it will remain on the order book until filled or canceled). The client’s create\_order method returns a signed order payload, and post\_order sends it to Polymarket[\[37\]](https://github.com/Polymarket/py-clob-client#:~:text=order%20%3D%20OrderArgs%28token_id%3D%22%3Ctoken,GTC%29%20print%28resp). If successful, result will contain an order ID (id) confirming the order on the book.

*Alternatively*, the client offers a one-step method create\_and\_post\_order() that signs and submits in one call[\[38\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=Ask%20AI)[\[39\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=funder%3Dos.getenv%28%22FUNDER_ADDRESS%22%29%20,). For example:

order \= {"token\_id": token\_id, "price": 0.65, "size": 10, "side": "BUY"}  
options \= {"tick\_size": "0.01", "neg\_risk": False}  
result \= client.create\_and\_post\_order(order, options)  
print("Order placed\! ID:", result\["orderID"\])

In this case, we explicitly specify the market’s tick size (minimum price increment, often 0.01 on Polymarket) and neg\_risk (whether using negative risk trading; here False for a standard order). The create\_and\_post\_order call wraps the two steps above into one. Use whichever approach you find more convenient.

**Note:** Even with API key auth, order creation **still requires a signature from your private key**. The client handles this for you (create\_order or create\_and\_post\_order uses your provided key to sign). Polymarket’s design ensures **non-custodial trading** – you control your funds and sign orders locally, while the CLOB matches orders off-chain and settles on-chain.

### Order Types and Execution

Polymarket supports limit orders and will match them against the order book:

* *If your limit price is immediately favorable* (e.g., you place a buy at a price higher than or equal to the current ask), it will execute immediately against existing orders (you become the taker for those trades).

* Otherwise, your order sits on the book as a maker until another order fills it. You can always check your open orders or cancel them.

You can retrieve your **open orders** via client.get\_orders() and cancel an order with client.cancel(order\_id) or cancel all via client.cancel\_all()[\[40\]](https://github.com/Polymarket/py-clob-client#:~:text=client)[\[41\]](https://github.com/Polymarket/py-clob-client#:~:text=open_orders%20%3D%20client). For example:

open\_orders \= client.get\_orders()  \# get all open orders for your account  
for order in open\_orders:  
    print("Open order:", order\["id"\], "@ price", order\["price"\])  
\# Cancel the first open order (if any)  
if open\_orders:  
    client.cancel(open\_orders\[0\]\["id"\])

This would list and then cancel one order. There are also batch cancel endpoints (e.g. cancel all orders or all orders in a specific market) should you need them.

## Saving Data to JSON or CSV

Often you’ll want to save Polymarket data for analysis or record-keeping. Python makes this easy:

* **Save markets or trades to JSON:** You can dump the Python dictionary or list of dicts directly to a JSON file using the json module.

* **Save to CSV:** For tabular data (like lists of trades or positions), you can use Python’s csv module or pandas to save as CSV.

Below are examples for both:

import json, csv

\# Assume we have fetched market data and trade history as Python objects:  
markets \= public\_client.get\_simplified\_markets()      \# dictionary with market info  
trades \= client.get\_trades()                          \# list of trade records (each a dict)

\# 1\. Save markets data to JSON file  
with open("polymarket\_markets.json", "w") as f:  
    json.dump(markets, f, indent=2)  
print("Saved markets to polymarket\_markets.json")

\# 2\. Save trade history to CSV file  
if trades:  
    keys \= trades\[0\].keys()  \# use the first trade's keys as CSV headers  
    with open("polymarket\_trades.csv", "w", newline="") as f:  
        writer \= csv.DictWriter(f, fieldnames=keys)  
        writer.writeheader()  
        writer.writerows(trades)  
    print(f"Saved {len(trades)} trades to polymarket\_trades.csv")

After running this, you’ll have polymarket\_markets.json containing the full JSON data of markets and polymarket\_trades.csv with columns like trade ID, market, outcome, price, size, timestamp, etc., for each of your trades. You can adjust the fields or formatting as needed (for example, using pandas.DataFrame for more complex analysis).

## Best Practices and Tips

When using the Polymarket API, keep in mind the following best practices to ensure efficient and secure usage:

* **Rate Limits:** Polymarket’s APIs are robust but do enforce rate limits to prevent abuse. The limits are quite high for public data (e.g., the CLOB API allows \~9000 requests per 10 seconds for general endpoints)[\[42\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=Endpoint%20Limit%20Notes%20CLOB%20,over%20the%20maximum%20configured%20rate). However, certain calls like trading endpoints have stricter limits (e.g., up to 500 order placements per second in bursts)[\[43\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=Endpoint%20Limit%20Notes%20CLOB%20POST,50%2Fs%29Throttle%20requests%20over). Exceeding these will result in throttling by the server[\[44\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=All%20rate%20limits%20are%20enforced,This%20means). Design your application to respect these limits – use batching or backoff when needed. For example, avoid polling the entire order book too frequently; consider using the WebSocket for live updates if you need real-time data.

* **Secure Key Management:** Treat your private key and API secret as highly sensitive. **Never commit private keys or API secrets to code repositories**[\[5\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=). Use environment variables or a secrets manager to load them at runtime. If possible, restrict your API key’s permissions (Polymarket’s API key is scoped to trading on your account only, but always keep your secret secure). If you suspect your API credentials are compromised, regenerate them (using the L1 method again) to invalidate the old keys.

* **Error Handling:** The API will return error messages or codes for invalid requests or issues:

* If you see L2\_AUTH\_NOT\_AVAILABLE, it means you haven’t set up the API credentials in the client. Make sure to call the API key creation and initialize the client with those creds before invoking protected endpoints[\[15\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Error%3A%20L2_AUTH_NOT_AVAILABLE).

* **Order Rejections:** An order can be rejected if you lack funds or allowance. An "insufficient balance" error indicates your **funder address** doesn’t have enough tokens for the trade (ensure you have enough USDC for buys or outcome tokens for sells)[\[35\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20balance). An "insufficient allowance" means you haven’t approved the exchange contract to spend your tokens – you may need to perform an approval transaction (via the Polymarket UI or directly calling the token contract) before trading[\[36\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20allowance).

* Always check the response of an API call. The Polymarket client will typically raise exceptions for HTTP errors or return an error structure. Use try/except blocks around critical operations like placing orders, and implement retries or user notifications as appropriate.

* **Testing and Dry Runs:** If you are writing a trading bot or integration, test with small trades first to ensure your logic works as expected. Polymarket operates on real value (USDC), so mistakes can be costly. There is no separate testnet API, but you can use minimal sizes or create markets with friends to simulate.

* **Stay Informed:** Polymarket may update APIs or add features. Keep an eye on the official documentation and changelog. Join the Polymarket Discord or follow their developer updates for any breaking changes. The docs include a full reference of client methods[\[45\]](https://docs.polymarket.com/developers/CLOB/clients/methods-overview#:~:text=Public%20Methods%20Access%20market%20data%2C,34) and examples[\[46\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Full%20Example%20Implementations%20Complete%20Next,CLOB%20and%20Builder%20Relay%20clients) – consult these for advanced usage like conditional orders, builder program tools, etc.

By following these best practices, you’ll ensure a smooth and secure experience building on Polymarket’s platform.

## Sources

1. **Polymarket Docs – Authentication (L1 vs L2):** Polymarket CLOB authentication levels and examples[\[6\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=The%20CLOB%20uses%20two%20levels,public%20methods%20and%20public%20endpoints)[\[7\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=L2%20Authentication).

2. **Polymarket Docs – Quickstart Guide:** Python integration for client setup, order placement, and troubleshooting tips[\[15\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Error%3A%20L2_AUTH_NOT_AVAILABLE)[\[35\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20balance).

3. **Polymarket Docs – Market Data (Gamma API):** How to fetch active events/markets and interpret outcome prices[\[18\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=List%20all%20currently%20active%20events,on%20Polymarket)[\[20\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Markets%20have%20,These%20prices%20represent%20implied%20probabilities).

4. **Polymarket Docs – CLOB API Endpoints:** Overview of key REST endpoints (price, book, order, etc.)[\[24\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Query%20the%20CLOB%20for%20the,current%20price%20of%20any%20token)[\[25\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Ask%20AI) and rate limits[\[47\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=Endpoint%20Limit%20Notes%20CLOB%20,over%20the%20maximum%20configured%20rate)[\[48\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=CLOB%20Trading%20Endpoints).

5. **Polymarket Docs – Data API Reference:** Endpoints for user positions and trade history (JSON schema of /positions response)[\[29\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=%7B%20,123)[\[30\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=,string).

6. **Polymarket GitHub – py-clob-client README:** Official Python client usage examples for market data, orders, and authentication[\[49\]](https://github.com/Polymarket/py-clob-client#:~:text=order%20%3D%20OrderArgs%28token_id%3D%22%3Ctoken,GTC%29%20print%28resp)[\[34\]](https://github.com/Polymarket/py-clob-client#:~:text=last%20%3D%20client.get_last_trade_price%28%22%3Ctoken,get_trades%28%29%20print%28last%2C%20len%28trades).

---

[\[1\]](https://github.com/Polymarket/py-clob-client#:~:text=,env) [\[2\]](https://github.com/Polymarket/py-clob-client#:~:text=Installation) [\[3\]](https://github.com/Polymarket/py-clob-client#:~:text=HOST%20%3D%20,Address%20that%20holds%20your%20funds) [\[4\]](https://github.com/Polymarket/py-clob-client#:~:text=PRIVATE_KEY%20%3D%20%22%3Cyour,Address%20that%20holds%20your%20funds) [\[13\]](https://github.com/Polymarket/py-clob-client#:~:text=) [\[14\]](https://github.com/Polymarket/py-clob-client#:~:text=HOST%20%3D%20,Address%20that%20holds%20your%20funds) [\[34\]](https://github.com/Polymarket/py-clob-client#:~:text=last%20%3D%20client.get_last_trade_price%28%22%3Ctoken,get_trades%28%29%20print%28last%2C%20len%28trades) [\[37\]](https://github.com/Polymarket/py-clob-client#:~:text=order%20%3D%20OrderArgs%28token_id%3D%22%3Ctoken,GTC%29%20print%28resp) [\[40\]](https://github.com/Polymarket/py-clob-client#:~:text=client) [\[41\]](https://github.com/Polymarket/py-clob-client#:~:text=open_orders%20%3D%20client) [\[49\]](https://github.com/Polymarket/py-clob-client#:~:text=order%20%3D%20OrderArgs%28token_id%3D%22%3Ctoken,GTC%29%20print%28resp) GitHub \- Polymarket/py-clob-client: Python client for the Polymarket CLOB

[https://github.com/Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)

[\[5\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=) [\[6\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=The%20CLOB%20uses%20two%20levels,public%20methods%20and%20public%20endpoints) [\[7\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=L2%20Authentication) [\[8\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=What%20is%20L2%3F) [\[9\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=What%20This%20Enables) [\[10\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=%2F%2F%20Gets%20API%20key%2C%20or,createOrDeriveApiKey) [\[11\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=%7B%20%22apiKey%22%3A%20%22550e8400,) [\[38\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=Ask%20AI) [\[39\]](https://docs.polymarket.com/developers/CLOB/authentication#:~:text=funder%3Dos.getenv%28%22FUNDER_ADDRESS%22%29%20,) Authentication \- Polymarket Documentation

[https://docs.polymarket.com/developers/CLOB/authentication](https://docs.polymarket.com/developers/CLOB/authentication)

[\[12\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Signature%20Types) [\[15\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Error%3A%20L2_AUTH_NOT_AVAILABLE) [\[35\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20balance) [\[36\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Order%20rejected%3A%20insufficient%20allowance) [\[46\]](https://docs.polymarket.com/developers/CLOB/quickstart#:~:text=Full%20Example%20Implementations%20Complete%20Next,CLOB%20and%20Builder%20Relay%20clients) Quickstart \- Polymarket Documentation

[https://docs.polymarket.com/developers/CLOB/quickstart](https://docs.polymarket.com/developers/CLOB/quickstart)

[\[16\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=etc.%29,quotes%2C%20and%20to%20submit%20or) [\[17\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=,retrieve%20nested%20data%20like%20the) [\[28\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=,API%20to%20retrieve%20all%20open) [\[33\]](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf#:~:text=logs,Gamma%20and%20CLOB%20APIs%20by) The Polymarket API: Architecture, Endpoints, and Use Cases | by Jung-Hua Liu | Jan, 2026 | Medium

[https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf)

[\[18\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=List%20all%20currently%20active%20events,on%20Polymarket) [\[19\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=) [\[20\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Markets%20have%20,These%20prices%20represent%20implied%20probabilities) [\[21\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=%7B%20,probability) [\[22\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=curl%20%22https%3A%2F%2Fgamma) [\[23\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Once%20you%20have%20an%20event%2C,using%20its%20ID%20or%20slug) [\[24\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Query%20the%20CLOB%20for%20the,current%20price%20of%20any%20token) [\[25\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Ask%20AI) [\[26\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=Get%20Orderbook%20Depth) [\[27\]](https://docs.polymarket.com/quickstart/fetching-data#:~:text=%7B%20,) Fetching Market Data \- Polymarket Documentation

[https://docs.polymarket.com/quickstart/fetching-data](https://docs.polymarket.com/quickstart/fetching-data)

[\[29\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=%7B%20,123) [\[30\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=,string) [\[31\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=) [\[32\]](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions#:~:text=%60) Get User Positions (Data-API) \- Polymarket Documentation

[https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions](https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions)

[\[42\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=Endpoint%20Limit%20Notes%20CLOB%20,over%20the%20maximum%20configured%20rate) [\[43\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=Endpoint%20Limit%20Notes%20CLOB%20POST,50%2Fs%29Throttle%20requests%20over) [\[44\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=All%20rate%20limits%20are%20enforced,This%20means) [\[47\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=Endpoint%20Limit%20Notes%20CLOB%20,over%20the%20maximum%20configured%20rate) [\[48\]](https://docs.polymarket.com/quickstart/introduction/rate-limits#:~:text=CLOB%20Trading%20Endpoints) API Rate Limits \- Polymarket Documentation

[https://docs.polymarket.com/quickstart/introduction/rate-limits](https://docs.polymarket.com/quickstart/introduction/rate-limits)

[\[45\]](https://docs.polymarket.com/developers/CLOB/clients/methods-overview#:~:text=Public%20Methods%20Access%20market%20data%2C,34) Methods Overview \- Polymarket Documentation

[https://docs.polymarket.com/developers/CLOB/clients/methods-overview](https://docs.polymarket.com/developers/CLOB/clients/methods-overview)