# Hyperliquid API Notes

**Project:** Hyper-Engine, developed under RookLab
**Purpose:** Reference notes for Hyperliquid exchange integration
**Primary scope:** HyperCore market data, account information, and trading operations

**Official documentation:** [Hyperliquid API Documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)

> **Important:** This document is an engineering reference, not a substitute for the current official API documentation. Endpoint behavior, signing requirements, rate limits, supported actions, and error formats can change. Verify details against the official documentation before implementing or modifying exchange interactions.

---

## 1. What Is Hyperliquid?

Hyperliquid is a blockchain ecosystem designed for financial applications, including on-chain perpetual futures and spot trading.

Its architecture includes several important components:

* **HyperBFT:** The consensus mechanism underlying the Hyperliquid L1.
* **HyperCore:** The trading system supporting perpetual futures and spot markets.
* **HyperEVM:** An Ethereum-compatible execution environment integrated with the Hyperliquid ecosystem.

HyperCore and HyperEVM have different responsibilities. HyperCore provides the exchange functionality relevant to Hyper-Engine's current trading focus, while HyperEVM supports smart-contract applications.

### Relevance to Hyper-Engine

Hyper-Engine primarily integrates with HyperCore through Hyperliquid's HTTP API.

The principal API endpoints are:

* `/info` — information queries.
* `/exchange` — signed exchange actions.

WebSocket interfaces can provide streaming market and account updates, but their implementation status must be assessed separately from the HTTP integration.

HyperEVM smart-contract development is outside the current scope of the core trading framework unless introduced as a separately approved feature.

---

## 2. Network Endpoints

The standard API base URLs are:

| Network | HTTP API                              | WebSocket API                          |
| ------- | ------------------------------------- | -------------------------------------- |
| Mainnet | `https://api.hyperliquid.xyz`         | `wss://api.hyperliquid.xyz/ws`         |
| Testnet | `https://api.hyperliquid-testnet.xyz` | `wss://api.hyperliquid-testnet.xyz/ws` |

Confirm the current endpoints in the official documentation before use.

### Development rule

Use testnet for exchange-integration development and testing whenever applicable.

Do not assume that testnet behavior perfectly reproduces mainnet behavior. Differences in liquidity, account state, market conditions, and execution outcomes can affect results.

Hyper-Engine's explicit execution safeguards must remain in place. Documentation work must not enable mainnet execution or weaken existing testnet gates.

---

## 3. HTTP API Structure

### 3.1 Information endpoint — `POST /info`

The information endpoint retrieves market, account, and historical data.

Common query types include:

| Query type               | Purpose                                         |
| ------------------------ | ----------------------------------------------- |
| `meta`                   | Perpetual market metadata and asset information |
| `spotMeta`               | Spot market metadata                            |
| `clearinghouseState`     | Perpetual account state                         |
| `spotClearinghouseState` | Spot account state                              |
| `openOrders`             | Open orders for an account                      |
| `userFills`              | Account trade and fill history                  |
| `orderStatus`            | Status information for a specified order        |
| `l2Book`                 | Order-book information for a market             |
| `allMids`                | Mid-price information for markets               |
| `fundingHistory`         | Historical funding information                  |

This list is illustrative rather than exhaustive. Consult the current documentation for supported query types and their required fields.

Information queries generally do not require the signed-action format used by `/exchange`. Some account queries require an account address, so the correct address must be supplied.

### Account-address considerations

When querying an account that uses an API wallet or agent wallet, distinguish the trading account from the signing wallet.

Use the account address required by the particular query. Do not assume that an API wallet address is interchangeable with the master account or trading account address.

An empty account response may indicate an incorrect address or an account with no relevant activity. It should not automatically be interpreted as an API failure.

### Historical queries and pagination

Some time-range queries have response-size limits.

When retrieving historical records:

1. Check the documented limit for the specific query.
2. Inspect the returned timestamps.
3. Advance the next request using the appropriate cursor or time boundary.
4. Handle overlapping boundaries and duplicate records.
5. Stop when the requested interval has been covered or the endpoint indicates there are no more records.

Do not assume that every endpoint uses the same limit or pagination convention.

### Example request body

```json
{
  "type": "clearinghouseState",
  "user": "0x..."
}
```

Use a valid account address in actual requests. The example above is illustrative and does not identify a real account.

---

## 4. Exchange endpoint — `POST /exchange`

The exchange endpoint accepts actions that change exchange or account state.

Examples include:

* Placing orders.
* Cancelling orders.
* Approving API wallets.
* Performing supported transfers or withdrawals.

Actions generally require a valid signature constructed according to the specific action's signing rules.

### General request structure

A typical signed request has the following conceptual structure:

```json
{
  "action": {},
  "nonce": 0,
  "signature": {
    "r": "0x...",
    "s": "0x...",
    "v": 27
  }
}
```

This is a structural illustration, not a complete executable request. The action fields, nonce, signature, and any additional required fields depend on the action being submitted.

### Common exchange actions

| Action type        | Purpose                                                  |
| ------------------ | -------------------------------------------------------- |
| `order`            | Submit one or more orders                                |
| `cancel`           | Cancel orders by exchange order ID                       |
| `cancelByCloid`    | Cancel orders by client order ID                         |
| `approveAgent`     | Approve an API wallet or agent                           |
| Transfer actions   | Transfer supported assets between permitted destinations |
| Withdrawal actions | Initiate supported withdrawals                           |

Action names and payload schemas must be verified against the current official documentation before implementation.

### Order time-in-force options

Common time-in-force values include:

| Value | Meaning                                                  |
| ----- | -------------------------------------------------------- |
| `GTC` | Good Till Canceled                                       |
| `ALO` | Add Liquidity Only, commonly used for post-only behavior |
| `IOC` | Immediate or Cancel                                      |

Their exact behavior depends on the order configuration and exchange rules. Verify current requirements before relying on a particular order type.

### Client order IDs

A client order ID, commonly called a CLOID, allows an application to associate its own identifier with an exchange order.

Before using CLOIDs:

* Follow the documented format and length constraints.
* Keep IDs unique where required.
* Persist the relationship between application IDs and exchange order IDs.
* Handle retries without accidentally duplicating orders.
* Use the supported cancellation method when cancelling by CLOID.

### Expiration fields

Some actions support an expiration field such as `expiresAfter`.

An expiration timestamp can cause an action to be rejected if it arrives too late. Its supported actions, timestamp requirements, and rate-limit consequences should be verified in the current documentation.

---

## 5. Asset IDs and Market Metadata

Asset identifiers should be derived from current exchange metadata rather than treated as permanent constants.

### Perpetual markets

Perpetual asset identifiers are associated with entries in the perpetual market universe returned by `meta`.

### Spot markets

Spot asset identifiers use the exchange's spot-market identifier scheme, which differs from the perpetual-market scheme.

The precise identifier should be derived from the current `spotMeta` response and its documented rules.

### Recommended implementation approach

At initialization:

1. Retrieve the required market metadata.
2. Validate the response structure.
3. Build a symbol-to-asset mapping.
4. Handle missing or unexpected symbols explicitly.
5. Refresh metadata according to the application's lifecycle and the exchange's requirements.

Avoid hardcoding asset indices based on assumptions about market ordering.

A metadata change must not silently cause a symbol to map to the wrong market.

---

## 6. Authentication and Signing

Hyperliquid's signed exchange actions use wallet-based cryptographic signing rather than the conventional API-key-and-secret model used by many centralized exchanges.

Hyper-Engine's authentication implementation includes EIP-712 signing and the signing logic required by the relevant Hyperliquid action types.

### Different signing paths

Not all signed actions use the same payload construction.

The relevant distinctions include:

* L1 action signing for applicable trading actions.
* User-signed action signing for applicable user-facing actions.

The required scheme depends on the action and its current protocol definition. Never select a signing scheme based solely on the HTTP endpoint.

### L1 action signing and the phantom agent

L1 action signing involves protocol-specific payload construction. Important considerations include:

* Action serialization.
* Field ordering where required by the protocol.
* Message hashing.
* Phantom-agent construction.
* EIP-712 domain and message fields.
* Nonce handling.

Small differences in serialization or message construction can produce a signature the exchange rejects.

### User-signed actions

Some user-facing actions use a distinct EIP-712 signing format, including the applicable signature-chain configuration.

Do not assume that the chain ID, domain, or payload fields for one signing scheme are interchangeable with those of another.

### Signing failure checklist

When a signed action fails, check:

1. Whether the correct signing scheme was selected.
2. Whether the action payload matches the current protocol definition.
3. Whether serialization and field ordering are correct.
4. Whether numeric values are represented in the expected format.
5. Whether the correct account and signing wallet are used.
6. Whether the nonce is valid.
7. Whether the EIP-712 domain and chain configuration are correct.
8. Whether the exchange response identifies a signing error or a different validation failure.

A locally recovered signer address does not, by itself, prove that the exchange will accept the request.

### Security principles

* Keep private keys out of source control.
* Never log private keys or secret configuration values.
* Restrict API-wallet permissions where supported.
* Use separate signing identities when required by the operational design.
* Do not assume an API wallet has identical permissions to a master wallet.
* Verify current exchange rules before relying on withdrawal restrictions or other permission boundaries.

---

## 7. Nonces and Replay Protection

Signed actions use nonces to help prevent replay and enforce ordering constraints.

The precise nonce rules depend on the current Hyperliquid protocol and action type.

### Implementation considerations

* Generate nonces according to the documented requirements.
* Use the required timestamp units where timestamp-based nonces apply.
* Ensure that the signed payload and outer request use consistent nonce values where required.
* Account for concurrent processes that share a signing identity.
* Handle nonce-related rejections explicitly.
* Avoid blindly resubmitting an action with an unchanged invalid nonce.

A retry must distinguish between an action that was rejected before execution and one whose execution outcome is uncertain.

If a request times out after submission, query the appropriate order or account state before retrying an action that could create a duplicate order.

---

## 8. API Wallets and Agent Wallets

API wallets, also called agent wallets in Hyperliquid documentation, allow an approved signing identity to submit supported actions on behalf of an account.

The exact permission model and supported actions should be verified against current official documentation.

### General considerations

* Keep the master wallet's private key separate from routine application configuration where practical.
* Use a dedicated signing identity for automated trading when appropriate.
* Approve the agent through the documented procedure.
* Use the correct account address for information queries.
* Protect the agent's private key as a sensitive credential.
* Understand the agent's current permissions and revocation process.

Do not assume that every API-wallet configuration has the same capabilities or restrictions.

### Operational safeguards

The application should:

* Load credentials from protected configuration.
* Fail safely when required credentials are absent or invalid.
* Avoid printing secrets in exceptions and logs.
* Verify network configuration before any permitted execution.
* Provide a controlled procedure for rotating or revoking compromised credentials.

---

## 9. Rate Limits

Hyperliquid applies rate limits to API usage. Limits can differ by endpoint, request type, address, and connection.

The exact numerical limits may change and should be checked in the official documentation before deployment.

### Rate-limit engineering

A robust client should:

* Handle HTTP and exchange-level rate-limit responses.
* Apply bounded retries where appropriate.
* Use backoff rather than repeatedly retrying immediately.
* Avoid retrying non-idempotent actions without checking their execution state.
* Limit unnecessary polling.
* Batch requests only where supported and appropriate.
* Track rate-limit errors and request latency.
* Keep cancellation and emergency procedures in mind when designing request budgets.

### WebSocket considerations

If a WebSocket client is used, account for connection limits, subscription limits, message-rate limits, reconnection behavior, and any documented restrictions on posting actions over the connection.

A working HTTP client does not establish that the application has a reliable streaming implementation.

---

## 10. Order and API Errors

Exchange errors can arise from request validation, market constraints, margin requirements, order state, or operational limits.

Examples of error categories include:

| Error category           | Possible meaning                                                           |
| ------------------------ | -------------------------------------------------------------------------- |
| Tick-size validation     | Price does not conform to market precision                                 |
| Minimum notional         | Order value is below the required minimum                                  |
| Insufficient margin      | Available margin does not support the order                                |
| Reduce-only validation   | An order violates reduce-only constraints                                  |
| Post-only rejection      | A post-only order would match immediately                                  |
| IOC cancellation         | The order did not execute fully and the remainder was cancelled            |
| Trigger-price validation | A trigger or conditional-order parameter is invalid                        |
| Insufficient liquidity   | The requested execution cannot be completed because of available liquidity |
| Position limit           | The order violates a position or margin-tier constraint                    |
| Missing order            | The order was not found in the requested state or lookup context           |

These examples are descriptive, not a complete or guaranteed list of current error strings.

### Batched requests

When an action contains multiple orders, inspect the actual response structure rather than assuming every response contains one result per order.

The client should account for:

* Per-order errors.
* Batch-level validation errors.
* Partial success where supported.
* Ambiguous execution outcomes.
* Orders that were accepted but not filled.
* Cancellation requests that refer to already-filled or already-cancelled orders.

Do not fabricate individual order outcomes from a batch-level error.

### Execution confirmation

An accepted request is not necessarily a filled order.

For execution paths that require confirmed fills, verify the appropriate exchange state or fill records before taking dependent actions.

For example, if the workflow requires a BUY to fill before a SELL can be submitted, an accepted BUY response alone must not satisfy that requirement.

---

## 11. WebSocket API

Hyperliquid provides a WebSocket interface for supported streaming data and other documented operations.

Depending on the subscription, it can provide market updates and account-related information.

### General subscription structure

A subscription request commonly follows this structure:

```json
{
  "method": "subscribe",
  "subscription": {}
}
```

The subscription object must match the specific channel's documented schema.

Some channels provide an initial snapshot before subsequent updates. Clients should distinguish initial snapshots from incremental updates where the channel defines that distinction.

### Engineering considerations

A reliable streaming implementation must account for:

* Connection establishment and closure.
* Reconnection after network failure.
* Subscription restoration.
* Message validation.
* Initial snapshots and incremental updates.
* Stale data.
* Duplicate or out-of-order observations where applicable.
* Rate and subscription limits.
* Graceful shutdown.

WebSocket messages should not be treated as permanently authoritative without considering their freshness and the channel's semantics.

### Hyper-Engine implementation status

WebSocket support must be described according to the current code and tests. A protocol capability or planned module is not proof that the framework currently uses WebSocket streaming in its primary trading pipeline.

The React + TypeScript dashboard currently uses the documented API polling approach. Do not describe the dashboard as WebSocket-driven unless that behavior has been implemented and verified.

---

## 12. Official SDKs and Reference Implementations

The following repositories are useful references. Check their maintainers, activity, and compatibility before adopting any dependency.

| Language   | Repository                                                                          | Notes               |
| ---------- | ----------------------------------------------------------------------------------- | ------------------- |
| Python     | [hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) | Official Python SDK |
| Rust       | [hypersdk](https://github.com/infinitefield/hypersdk)                               | Community project   |
| TypeScript | [hyperliquid](https://github.com/nktkas/hyperliquid)                                | Community project   |
| TypeScript | [hyperliquid](https://github.com/nomeida/hyperliquid)                               | Community project   |

SDKs can help cross-check protocol details, but their behavior should not be assumed to match the project's implementation automatically.

Hyper-Engine's own tests should verify the exact payloads and safeguards required by its execution path.

---

## 13. Practical Integration Checklist

Before changing or extending the Hyperliquid integration, verify the following.

### Market data

* [ ] Use the correct endpoint and query type.
* [ ] Validate the response structure.
* [ ] Handle empty and malformed responses.
* [ ] Use current market metadata.
* [ ] Handle pagination and timestamp boundaries.
* [ ] Avoid assuming historical data is complete or error-free.

### Authentication

* [ ] Select the correct signing scheme.
* [ ] Validate payload construction and serialization.
* [ ] Use the required EIP-712 configuration.
* [ ] Generate valid nonces.
* [ ] Keep credentials out of logs and version control.
* [ ] Verify account and signing-wallet roles.

### Orders and execution

* [ ] Validate symbol-to-asset mappings.
* [ ] Validate price and quantity precision.
* [ ] Handle minimum-notional and margin constraints.
* [ ] Distinguish submission acceptance from confirmed fills.
* [ ] Handle partial fills and cancellations correctly.
* [ ] Avoid blind retries when the outcome is uncertain.
* [ ] Preserve existing testnet execution gates.
* [ ] Never bypass the approved execution path.

### Operations

* [ ] Handle timeouts and rate limits.
* [ ] Use bounded retry and backoff behavior.
* [ ] Log relevant failures without exposing secrets.
* [ ] Verify persistent state before relying on recovered data.
* [ ] Test against testnet before considering any broader deployment.
* [ ] Review the current official documentation when protocol details may have changed.

---

## 14. Key Engineering Principles

The most important principles for Hyper-Engine's exchange integration are:

1. **Use current metadata.** Do not rely on permanently hardcoded asset indices.
2. **Treat signing as protocol-specific.** Different action types may require different payloads and signing schemes.
3. **Treat nonces carefully.** Retry behavior must account for whether an action may already have been accepted.
4. **Separate order acceptance from execution.** A successful submission does not prove a fill.
5. **Validate every response.** Handle both batch-level and individual-order failures where applicable.
6. **Preserve risk safeguards.** Exchange connectivity must not bypass risk checks or execution gates.
7. **Protect credentials.** Signing keys and private configuration must remain outside public source control.
8. **Treat protocol limits as changeable.** Verify current rate limits, order rules, and signing requirements.
9. **Distinguish planned functionality from implemented functionality.** Documentation must reflect what the code actually does.
10. **Test incrementally.** Use unit tests, mocked exchange responses, and testnet validation as appropriate.

---

## Reference

For authoritative and current protocol details, consult the [official Hyperliquid API documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api).

Hyper-Engine is developed under RookLab as a trading-systems engineering and research project. Exchange integration, backtesting, and testnet execution are engineering capabilities; they do not guarantee profitability or establish mainnet production readiness.
