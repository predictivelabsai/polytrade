# AssetHero API authentication contract

PolyTrade is a standalone Clerk application. AssetHero is a separate application
that can optionally call PolyTrade's gateway, agent, and backtest APIs using its
own identity system. AssetHero is not a PolyTrade frontend authentication mode.

Enable the integration by configuring both values on the gateway, agent,
backtest API, and worker:

```env
ASSETHERO_API_ISSUER=https://<assethero-issuer>
ASSETHERO_API_JWKS_URL=https://<assethero-jwks-endpoint>
ASSETHERO_API_AUDIENCE=polytrade
```

Leave the issuer and JWKS URL empty to run PolyTrade with Clerk only. Supplying
only one is a startup error. PolyTrade never receives AssetHero's signing key.

After its normal login, AssetHero obtains a short-lived user JWT for the
`polytrade` audience and sends it to the shared API origin. The gateway forwards
agent and backtest paths to the service that validates the token:

```http
Authorization: Bearer <assethero-user-jwt>
```

Required claims:

| Claim | Contract |
| --- | --- |
| `alg` | `RS256` only |
| `kid` | Required; resolves through AssetHero's configured JWKS |
| `iss` | Exact `ASSETHERO_API_ISSUER` value |
| `aud` | `polytrade` |
| `sub` | Stable AssetHero user identifier |
| `iat`, `exp` | Valid with 30 seconds skew; lifetime at most five minutes |
| `jti` | Required unique token identifier |
| `scope` | Space-delimited `research` and optionally `trade` |

The canonical owner is `assethero:<sub>`. It is deliberately separate from a
PolyTrade Clerk owner such as `clerk:<sub>`; matching email addresses do not
link accounts. The `trade` scope authorizes wallet/order API access but never
substitutes for wallet signatures. The gateway applies no geographic check;
geographic eligibility is the calling application's responsibility.

If AssetHero's browser calls PolyTrade directly, its exact HTTPS origin must be
listed in `CORS_ORIGINS`. Backend-to-backend requests do not require CORS.
Tokens must not be placed in URLs, local storage, logs, or HTML attributes.
