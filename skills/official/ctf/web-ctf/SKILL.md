---
name: ctf-web
description: CTF web challenge solving — parameter fuzz clues, source analysis, flag patterns.
tags: [ctf, web, exploit]
---

# CTF Web

Use `/subagents run ctf` with challenge URL or description.

## Approach
1. Identify challenge type (web/crypto/misc)
2. Recon the challenge app
3. Test injection/XSS/SSTI vectors with safe payloads
4. Document flag candidate locations
