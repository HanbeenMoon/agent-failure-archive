# What the x402 market actually looks like, measured

Measured 2026-08-25 while trying to sell one dollar of anything over x402. Every number here
came from a public endpoint you can call yourself; the calls are included so you can check the
numbers rather than trust them. Nothing here is modelled, projected, or averaged across sources.

## 1. The revenue is real and extremely concentrated

`POST`-free public tRPC on x402scan:

```bash
curl -s 'https://www.x402scan.com/api/trpc/public.sellers.all.list?input=%7B%22json%22%3A%7B%22pagination%22%3A%7B%22page%22%3A0%2C%22pageSize%22%3A15%7D%2C%22timeframe%22%3A30%7D%7D'
```

`timeframe` is in days and only values with a materialised view exist. 1, 7 and 30 work; 90
returns `relation "recipient_stats_aggregated_90d" does not exist`.

Top sellers, 30 days:

| rank | transactions | revenue | per call |
|---:|---:|---:|---:|
| 1 | 7,802,976 | $189,707 | $0.0243 |
| 2 | 3,316,817 | $38,753 | $0.0117 |
| 3 | 947,181 | $1,900 | $0.0020 |
| 4 | 347,010 | $347 | $0.0010 |
| **5** | **124,985** | **$105,615** | **$0.8450** |

Rank 5 matters more than rank 1 for anyone deciding what to charge. The modal price in this
market is a tenth of a cent to three cents, but a seller doing **$0.85 per call at 125,000
calls a month** exists. A dollar-scale price point is not theoretical here.

## 2. Almost nobody is shopping

Same API, buyer side. The field that matters is `unique_sellers`.

| buyer | transactions | spent | distinct sellers |
|---|---:|---:|---:|
| `0x2b4ee338…` | 7,776,842 | $188,470 | 1 |
| `BhFRCUXHVm…` | 3,280,471 | $38,059 | 1 |
| `0x8f9ac214…` | 302,509 | $605 | 1 |
| `0x391bf5a6…` | 100,139 | $200 | 1 |
| **`0x1cb8d145…`** | **41,261** | **$127** | **2,839** |
| `0xa39c469c…` | 40,216 | $249 | 4 |

Eight of the top ten buyers pay exactly one seller. That is not a marketplace, it is a set of
vertically integrated products whose clients happen to settle onchain. The volume figures in
ecosystem write-ups are almost entirely this.

The exception is real and worth knowing about: one address paid **2,839 distinct sellers**,
about 14.5 calls each, most recently 2026-08-23. Something is walking the directory and paying
as it goes. For a new seller that address is the most plausible first customer in the entire
market.

## 3. The discovery channel most people assume exists is dormant

x402scan runs a chat that invokes listed tools with a server wallet. It is the obvious answer to
"how will an agent find my service". It has not been used in months.

```bash
curl -s 'https://www.x402scan.com/api/trpc/public.tools.top?input=%7B%22json%22%3A%7B%22pagination%22%3A%7B%22page%22%3A0%2C%22pageSize%22%3A100%7D%7D%7D'
```

Ten tools have ever been called through it, 22,185 calls in total, and the most recent call was
**2026-05-09**. Over the same window the chain carried millions of x402 transactions. Money in
this market does not move through directory chat. It moves because a developer decided to
integrate a specific service.

## 4. Getting into the facilitator directory does not require a sale

PayAI's facilitator publishes a directory of 26,626 resources:

```bash
curl -s 'https://facilitator.payai.network/discovery/resources?limit=100&offset=0'
```

It is `GET`/`HEAD` only. Six plausible registration paths all return 404, and the Python SDK's
`initialize()` only fetches `/supported`, so there is no announce step anywhere in the protocol
we could find.

The obvious conclusion, that only sellers with completed sales appear, is **wrong**. A 1,500
entry sample contains 116 endpoints that cannot plausibly have had a paying customer: four
`v0-x402-*.vercel.app` scaffolds, several `*.trycloudflare.com` development tunnels, and
endpoints literally named `x402/schema-test`, `x402/demo` and `testnet-canary`. The entries
carry `inputSchema`, `outputSchema` and `toolName`, which are exactly the bazaar extension
fields from a 402 challenge. The directory appears to be built from payment attempts the
facilitator observes, not from settlements it completes. One attempt looks sufficient.

## 5. Discovery in this ecosystem is usage-gated, everywhere

This is the finding that reframes everything above, and it is not documented anywhere I could
find.

x402scan's discovery search does not return your service until it has usage. Not because of
ranking. It is an explicit filter, visible in
[`lib/discover/search.ts`](https://github.com/Merit-Systems/x402scan/blob/main/apps/scan/src/lib/discover/search.ts):

```ts
// broad=true would include resources without usage signals; pure embedding
// similarity then surfaces low-quality origins. Restricting to hasUsage
// resources cuts the noise.
url.searchParams.set('broad', 'false');
```

I confirmed it from the outside first. Our origin's description literally contains the words
"186 post-mortems", our routes are indexed, and `public.discover.search` returns nothing of ours
for `post-mortem`, `agent failures`, `debugging`, `reliability`, or `why did my agent fail`.
Ten to fifteen results each time, none of them us.

Stack that against the other two doors:

| Door | Opens when |
|---|---|
| x402scan discovery search | your resource has usage signals |
| PayAI directory, 26,626 resources | the facilitator observes one payment attempt |
| CDP Bazaar | one paid call through the CDP facilitator |

Every machine-readable path into this market is gated on a transaction you cannot have yet.
The filter is a reasonable defence against listing spam and I am not arguing against it. But
the consequence is worth stating plainly: **a new x402 seller is invisible to every automated
discovery surface until someone who already knows the URL pays.**

What is left is the set of doors a human walks through: curated lists, forum posts, the MCP
registry, GitHub search. All slow, all human-reviewed, none of them a place an agent with a
wallet is browsing. If you are planning to launch a paid endpoint and expecting agents to find
it, plan for that first transaction to arrive through a person instead.

## 6. Two things that will cost you an afternoon

**Your listing shows your function names.** If you do not set `description` on each route, the
x402 SDK forwards whatever your framework generated. Ours read `Search`, `Brief`, `Archive` in
a directory of hundreds of competitors, which tells a buyer nothing. Set `description`,
`service_name` and `tags` in the route config.

**A successful re-registration and an unchanged listing are compatible.** After fixing those
descriptions, re-registering reported four resources written while the list endpoint kept
showing the old text. Querying the record by id showed the new values had landed minutes
earlier. The list was a cached view. Send `Cache-Control: no-store` on `/openapi.json` and
`/.well-known/x402` so indexers refetch, and verify by id rather than by list.

**Also, the bazaar extension needs two fields.** `{"info": ...}` alone is malformed; the
validator wants `info` and `schema`, warns once at startup, and then silently drops the
extension. Use `x402.extensions.bazaar.declare_discovery_extension()` and let it build the
shape.

## Reproducing

Every call above is unauthenticated and read-only. The measurement script that produced the
seller table, the channel liveness check and the directory sample is
[`x402_market.py`](https://github.com/HanbeenMoon/agent-failure-archive) in the same project;
it prints the same numbers and will disagree with this document as the market moves. This
snapshot is 2026-08-25 and will age badly, which is the point of including the calls.
