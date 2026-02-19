# Mobile Steps Sync (Backend Contract)

This backend accepts daily or hourly step buckets and computes sneaker attribution server-side.
Timezone handling is bucket-specific to support travel and DST.

## Endpoints
- `POST /api/steps/buckets` (auth required)
- `POST /api/attribution/recompute` (auth required)

## Payload: UTC boundaries (preferred)
```json
{
  "source": "apple_health",
  "timezone": "America/Los_Angeles",
  "granularity": "day",
  "buckets": [
    {
      "start": "2026-01-12T08:00:00Z",
      "end": "2026-01-13T08:00:00Z",
      "steps": 8421
    }
  ]
}
```

## Payload: Local date buckets (fallback)
```json
{
  "source": "apple_health",
  "timezone": "America/Los_Angeles",
  "granularity": "day",
  "buckets": [
    {
      "date": "2026-01-12",
      "steps": 8421
    }
  ]
}
```

## Timezone Rules
- `timezone` must be an IANA timezone string (e.g., `Europe/London`, `America/Los_Angeles`).
- If `timezone` is omitted, the backend falls back to `User.timezone` (default `Europe/London`).
- Each bucket stores its timezone to support travel.
- Attribution uses the bucket timezone to derive the local date.

## DST Safety
- The backend does not assume 24-hour days.
- The device provides the bucket boundaries; the server trusts those boundaries.

## Mobile API Tokens
Create a token in Profile → Mobile API Tokens.
- The token is shown once at creation time—copy it immediately.
- Use it in the mobile app as:
  `Authorization: Bearer <token>`
- Revoked tokens are rejected.
