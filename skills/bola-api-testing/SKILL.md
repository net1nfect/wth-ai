---
name: bola-api-testing
description: Uji BOLA/IDOR pada API dengan matriks identitas × objek, tanpa CVE.
tags: [api, authorization, bola, idor]
tools: []
---

# BOLA / IDOR testing

Objective: prove that an object belonging to identity A can be read or modified
by identity B through the API, using only authorized test accounts.

## Preconditions

- Two accounts of the same role: A and B.
- One object owned by A with a known identifier.
- Authorization confirmed and scope CIDR recorded.

## Matrix

For every endpoint discovered and every object id in the collection:

| identity | object | method | expected | actual |
|---|---|---|---|---|
| A | A's | GET | 200 | 200 |
| A | B's | GET | 403 | ? |
| B | A's | GET | 403 | ? |
| B | A's | DELETE | 403 | ? |
| anon | A's | GET | 401 | ? |

A row where `actual` differs from `expected` is a candidate, not a finding.

## Evidence required before calling it a finding

1. baseline request/response,
2. mutated request/response,
3. negative control: the same request on your own object must behave correctly,
4. replay five times, all consistent,
5. impact captured on exactly one object — never a bulk dump.

## Common misses

- identifier type swap (int vs uuid, sequential vs hashed),
- nested resources where the parent is checked but the child is not,
- batch endpoints (`/orders?ids=...`) that skip per-object authorization,
- GraphQL node(id:) lookups that bypass the resolver guard,
- soft-deleted objects still reachable.

## Stop conditions

Rate limit from the scope card, target error rate above 5%, or kill switch.
Never persist, never exfiltrate, never modify more than the single proof object.
