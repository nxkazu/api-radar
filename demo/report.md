# API Radar report — petstore

Generated 2026-11-21 11:12 UTC by [API Radar](https://github.com/nxkazu/api-radar).

| | |
|---|---|
| Spec | `petstore` |
| Source | `demo/before.yaml` |
| Base version | `1.4.0` |
| Head version | `2.0.0` |
| Fingerprint | `f5a89bef089f3478` |

## Summary

| Severity | Count |
|---|---:|
| 🔴 Breaking | 21 |
| 🟠 Needs review | 6 |
| 🟢 Non-breaking | 23 |
| **Total** | **50** |

**Verdict: breaking changes detected.** Upgrading without code changes can break clients.

## 🔴 Breaking changes (21)

#### 1. DELETE /pets/{petId}

DELETE /pets/{petId} was removed

- Kind: `operation.removed`
- Suggested action: Migrate or remove call-sites for this operation before adopting the new spec.

#### 2. GET /pets › parameter query.page_size

New required query parameter 'page_size'

- Kind: `parameter.required.added`
- Before: —
- After: `True`
- Suggested action: Send the now-required parameter in every call.

#### 3. GET /pets › parameter query.status

Enum values removed: ['"pending"']

- Kind: `schema.enum.value.removed`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"available"', '"sold"']`
- Suggested action: Replace the removed enum value with a supported alternative.

#### 4. GET /pets › response 200 › application/json › items › properties.status

Enum values added: ['"archived"']

- Kind: `schema.enum.value.added`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Update exhaustive enum handling for the new value.

#### 5. GET /pets › response 200 › application/json › items › properties.tag

Property 'tag' was removed

- Kind: `schema.property.removed`
- Before: `{'type': 'string', 'nullable': True}`
- After: —
- Suggested action: Remove reads of this property; it is no longer part of the payload.

#### 6. GET /pets/{petId} › response 200 › application/json › properties.status

Enum values added: ['"archived"']

- Kind: `schema.enum.value.added`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Update exhaustive enum handling for the new value.

#### 7. GET /pets/{petId} › response 200 › application/json › properties.tag

Property 'tag' was removed

- Kind: `schema.property.removed`
- Before: `{'type': 'string', 'nullable': True}`
- After: —
- Suggested action: Remove reads of this property; it is no longer part of the payload.

#### 8. GET /pets/{petId} › response 404

Response 404 was removed

- Kind: `response.removed`
- Suggested action: Handle the removed status code: it is no longer part of the contract.

#### 9. GET /v1/owners/{ownerId}

GET /v1/owners/{ownerId} was removed

- Kind: `operation.removed`
- Suggested action: Migrate or remove call-sites for this operation before adopting the new spec.

#### 10. POST /pets › requestBody › application/json › properties.created_at

Property 'created_at' became required

- Kind: `schema.required.added`
- Before: `False`
- After: `True`
- Suggested action: Always provide this property.

#### 11. POST /pets › requestBody › application/json › properties.microchip

Property 'microchip' became required

- Kind: `schema.required.added`
- Before: `False`
- After: `True`
- Suggested action: Always provide this property.

#### 12. POST /pets › requestBody › application/json › properties.name

maxLength changed from 64 to 32

- Kind: `schema.constraint.tightened`
- Before: `64`
- After: `32`
- Suggested action: Validate inputs against the tighter constraint before calling.

#### 13. POST /pets › requestBody › application/json › properties.owner › properties.pets › items › properties.created_at

Property 'created_at' became required

- Kind: `schema.required.added`
- Before: `False`
- After: `True`
- Suggested action: Always provide this property.

#### 14. POST /pets › requestBody › application/json › properties.owner › properties.pets › items › properties.name

maxLength changed from 64 to 32

- Kind: `schema.constraint.tightened`
- Before: `64`
- After: `32`
- Suggested action: Validate inputs against the tighter constraint before calling.

#### 15. POST /pets › requestBody › application/json › properties.owner › properties.pets › items › properties.status

Enum values removed: ['"pending"']

- Kind: `schema.enum.value.removed`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Replace the removed enum value with a supported alternative.

#### 16. POST /pets › requestBody › application/json › properties.status

Enum values removed: ['"pending"']

- Kind: `schema.enum.value.removed`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Replace the removed enum value with a supported alternative.

#### 17. POST /pets › response 201 › application/json › properties.status

Enum values added: ['"archived"']

- Kind: `schema.enum.value.added`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Update exhaustive enum handling for the new value.

#### 18. POST /pets › response 201 › application/json › properties.tag

Property 'tag' was removed

- Kind: `schema.property.removed`
- Before: `{'type': 'string', 'nullable': True}`
- After: —
- Suggested action: Remove reads of this property; it is no longer part of the payload.

#### 19. POST /store/orders › parameter header.X-Idempotency-Key

header parameter 'X-Idempotency-Key' became required

- Kind: `parameter.required.added`
- Before: `False`
- After: `True`
- Suggested action: Send the now-required parameter in every call.

#### 20. POST /store/orders › response 400

Response 400 was removed

- Kind: `response.removed`
- Suggested action: Handle the removed status code: it is no longer part of the contract.

#### 21. document

New security requirement: {"apiKey": []}

- Kind: `security.requirement.added`
- Before: —
- After: `{'apiKey': []}`
- Suggested action: Supply the new authentication requirement on every call.

## 🟠 Needs review (6)

#### 22. GET /pets

operationId changed from 'listPets' to 'listAllPets'

- Kind: `operation.operation_id.changed`
- Before: `listPets`
- After: `listAllPets`
- Suggested action: Regenerate any generated client: the operationId changed.

#### 23. GET /pets/{petId} › response 200 › header X-Rate-Limit

Response header 'X-Rate-Limit' was removed

- Kind: `response.header.removed`
- Suggested action: Stop depending on this response header.

#### 24. POST /pets › requestBody › application/json › properties.owner › properties.pets › items › properties.tag

Property 'tag' was removed

- Kind: `schema.property.removed`
- Before: `{'type': 'string', 'nullable': True}`
- After: —
- Suggested action: Remove reads of this property; it is no longer part of the payload.

#### 25. POST /pets › requestBody › application/json › properties.tag

Property 'tag' was removed

- Kind: `schema.property.removed`
- Before: `{'type': 'string', 'nullable': True}`
- After: —
- Suggested action: Remove reads of this property; it is no longer part of the payload.

#### 26. document

Server URL removed: https://api.petstore.dev/v1

- Kind: `server.removed`
- Before: `https://api.petstore.dev/v1`
- After: —
- Suggested action: Point clients at a supported server URL.

#### 27. document

Server URL removed: https://staging.api.petstore.dev/v1

- Kind: `server.removed`
- Before: `https://staging.api.petstore.dev/v1`
- After: —
- Suggested action: Point clients at a supported server URL.

## 🟢 Non-breaking changes (23)

#### 28. GET /pets › parameter query.limit

maximum changed from 100 to 200

- Kind: `schema.constraint.loosened`
- Before: `100`
- After: `200`

#### 29. GET /pets › response 200 › application/json › items › properties.created_at

Property 'created_at' was added

- Kind: `schema.property.added`
- Before: —
- After: `{'type': 'string', 'format': 'date-time'}`

#### 30. GET /pets › response 200 › application/json › items › properties.created_at

Property 'created_at' became required

- Kind: `schema.required.added`
- Before: `False`
- After: `True`
- Suggested action: Always provide this property.

#### 31. GET /pets › response 200 › application/json › items › properties.name

maxLength changed from 64 to 32

- Kind: `schema.constraint.tightened`
- Before: `64`
- After: `32`
- Suggested action: Validate inputs against the tighter constraint before calling.

#### 32. GET /pets › response 200 › application/json › items › properties.status

Enum values removed: ['"pending"']

- Kind: `schema.enum.value.removed`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Replace the removed enum value with a supported alternative.

#### 33. GET /pets › response 200 › text/csv

Media type 'text/csv' was added

- Kind: `response.media_type.added`

#### 34. GET /pets/{petId} › parameter path.petId

Format changed from 'uuid' to None

- Kind: `schema.format.changed`
- Before: `uuid`
- After: —
- Suggested action: Check serialisation/parsing for the changed format.

#### 35. GET /pets/{petId} › response 200 › application/json › properties.created_at

Property 'created_at' was added

- Kind: `schema.property.added`
- Before: —
- After: `{'type': 'string', 'format': 'date-time'}`

#### 36. GET /pets/{petId} › response 200 › application/json › properties.created_at

Property 'created_at' became required

- Kind: `schema.required.added`
- Before: `False`
- After: `True`
- Suggested action: Always provide this property.

#### 37. GET /pets/{petId} › response 200 › application/json › properties.name

maxLength changed from 64 to 32

- Kind: `schema.constraint.tightened`
- Before: `64`
- After: `32`
- Suggested action: Validate inputs against the tighter constraint before calling.

#### 38. GET /pets/{petId} › response 200 › application/json › properties.status

Enum values removed: ['"pending"']

- Kind: `schema.enum.value.removed`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Replace the removed enum value with a supported alternative.

#### 39. GET /v2/owners/{ownerId}

GET /v2/owners/{ownerId} was added

- Kind: `operation.added`

#### 40. POST /pets › requestBody › application/json › properties.created_at

Property 'created_at' was added

- Kind: `schema.property.added`
- Before: —
- After: `{'type': 'string', 'format': 'date-time'}`

#### 41. POST /pets › requestBody › application/json › properties.owner › properties.pets › items › properties.created_at

Property 'created_at' was added

- Kind: `schema.property.added`
- Before: —
- After: `{'type': 'string', 'format': 'date-time'}`

#### 42. POST /pets › requestBody › application/json › properties.owner › properties.pets › items › properties.status

Enum values added: ['"archived"']

- Kind: `schema.enum.value.added`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Update exhaustive enum handling for the new value.

#### 43. POST /pets › requestBody › application/json › properties.status

Enum values added: ['"archived"']

- Kind: `schema.enum.value.added`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Update exhaustive enum handling for the new value.

#### 44. POST /pets › response 201 › application/json › properties.created_at

Property 'created_at' was added

- Kind: `schema.property.added`
- Before: —
- After: `{'type': 'string', 'format': 'date-time'}`

#### 45. POST /pets › response 201 › application/json › properties.created_at

Property 'created_at' became required

- Kind: `schema.required.added`
- Before: `False`
- After: `True`
- Suggested action: Always provide this property.

#### 46. POST /pets › response 201 › application/json › properties.name

maxLength changed from 64 to 32

- Kind: `schema.constraint.tightened`
- Before: `64`
- After: `32`
- Suggested action: Validate inputs against the tighter constraint before calling.

#### 47. POST /pets › response 201 › application/json › properties.status

Enum values removed: ['"pending"']

- Kind: `schema.enum.value.removed`
- Before: `['"available"', '"pending"', '"sold"']`
- After: `['"archived"', '"available"', '"sold"']`
- Suggested action: Replace the removed enum value with a supported alternative.

#### 48. POST /store/orders › response 409

Response 409 was added

- Kind: `response.added`

#### 49. document

Spec version 1.4.0 → 2.0.0

- Kind: `info.version`
- Before: `1.4.0`
- After: `2.0.0`

#### 50. document

Server URL added: https://api.petstore.dev/v2

- Kind: `server.added`
- Before: —
- After: `https://api.petstore.dev/v2`

## Call-sites found in the repository

### `/pets/{petId}` — 2 reference(s)
- `demo/callsites/petstore_client.py:24`
- `demo/callsites/petstore_client.py:29`

### `/v1/owners/{ownerId}` — 1 reference(s)
- `demo/callsites/petstore_client.py:33`

## How to use this report

1. Start with the breaking changes; each one lists the exact location in the spec.
2. Search the repository for the affected paths/fields (the call-site inventory above is generated automatically when the source is a GitHub repository).
3. Upgrade the client, run the test suite, then update the tracked spec version in API Radar.

---

_API Radar compares OpenAPI documents semantically. It resolves local `$ref`s, flattens `allOf`, and classifies each difference by direction: what is safe for a request may be breaking for a response._
