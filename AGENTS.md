Fase 4 diterima. Gas lanjut Fase 5 versi advanced.

Fase 5 = Real-Time Recon Platform Engine + 200+ Tool Registry + Job Handler Integration.

Target utama:
- wth_ai/ saja
- jangan gabungkan legacy core/
- testing tetap terminal/CLI-first
- jangan implement dashboard/updater/license/cloud agent
- jangan ubah behavior Fase 1-4 yang sudah hijau

Tujuan:
Handler recon yang sekarang masih mock harus diganti menjadi recon platform nyata, aman, legal, non-destruktif, real-time capable, dan support 200+ recon tools melalui tool registry, adapter, availability check, execution planner, result normalizer, scoring, diff, dan report.

Batas keras:
- Authorized target only
- Recon-only
- Non-destructive
- Tidak exploit
- Tidak brute force login
- Tidak bypass OTP/captcha
- Tidak memakai secret/token yang ditemukan
- Tidak download data sensitif
- Tidak aggressive fuzzing
- Semua request lewat policy engine, scope check, rate limit, allowlist, dan audit log
- Tools berisiko tinggi harus disabled_by_default

Arsitektur target:

User Command / Job
  -> Policy Engine
  -> Job Manager
  -> Recon Engine
  -> Tool Planner
  -> Tool Registry 200+
  -> Tool Runner / Builtin Module / API Adapter
  -> Result Normalizer
  -> Asset Graph / Evidence Store
  -> Scoring / Diff Engine
  -> Report Writer
  -> Event Bus
  -> Integration Manager

1. Module baru

Buat struktur:

wth_ai/recon/
  __init__.py
  engine.py
  model.py
  registry.py
  planner.py
  runner.py
  normalizer.py
  realtime.py
  diff.py
  dns.py
  http.py
  crawler.py
  js.py
  params.py
  headers.py
  tls.py
  cloud.py
  fingerprint.py
  scoring.py
  report.py
  wordlists.py
  tools/
    __init__.py
    catalog.py
    adapters.py
    builtin.py

2. Tool Registry 200+

Buat ToolSpec model:

{
  "name": "subfinder",
  "category": "subdomain",
  "capabilities": ["passive_subdomain"],
  "execution_mode": "external|builtin|api|manual|stub",
  "binary_names": ["subfinder"],
  "enabled_by_default": true,
  "risk_level": "safe|medium|high",
  "requires_network": true,
  "requires_api_key": false,
  "api_key_ref": null,
  "allowed_profiles": ["recon-only", "default", "high-quality"],
  "output_types": ["subdomain"],
  "rate_limited": true,
  "notes": "passive subdomain enumeration"
}

Registry wajib punya minimal 200 ToolSpec entries.

Important:
- Tidak semua tools harus runnable sekarang.
- Tools yang belum diimplement boleh execution_mode=stub/manual.
- Tapi registry, metadata, availability check, dan planner harus support 200+.
- Core safe tools wajib runnable/builtin kalau dependency tersedia.
- External tools wajib dicek via PATH, jangan crash kalau tidak terinstall.

3. Kategori 200+ tools

Catalog minimal harus mencakup kategori ini:

A. Core system/web utilities
- curl
- httpie
- wget
- dig
- host
- nslookup
- whois
- openssl
- jq
- rg
- grep
- python-requests
- python-dnspython
- python-httpx
- python-ssl
- python-beautifulsoup
- python-lxml
- playwright
- chromium
- sqlite-utils
- yq

B. Subdomain & asset discovery
- subfinder
- amass
- assetfinder
- findomain
- sublist3r
- knockpy
- github-subdomains
- gitlab-subdomains
- chaos-client
- crobat
- cero
- haktrails
- theHarvester
- metabigor
- crtsh
- certspotter
- censys-subdomain-finder
- rapiddns
- riddler
- sonar
- dnsdumpster
- netlas
- shodan-cli
- censys-cli
- securitytrails-cli
- binaryedge-cli
- zoomeye-cli
- fofa-cli
- hunter
- urlscan-cli

C. DNS validation & permutation
- dnsx
- puredns
- shuffledns
- massdns
- zdns
- dnsvalidator
- dnsrecon
- dnsenum
- fierce
- dnsmap
- dnsgen
- altdns
- gotator
- goaltdns
- ripgen
- dnsprobe
- dnspeep
- dnswalk
- dnstracer
- dnsviz
- checkdmarc
- dmarc-cat
- spoofy
- mailspoof
- mxtoolbox-api
- spfquery
- dkimsign
- dkimpy

D. HTTP probing & web metadata
- httpx
- httprobe
- hakcheckurl
- meg
- xurlfind3r
- httping
- htrace
- webanalyze
- whatweb
- wappalyzer
- wafw00f
- fav-up
- faviconhash
- mmh3
- retire
- retirejs
- builtwith-cli
- cmseek
- droopescan
- wpscan-passive
- joomscan-passive
- nikto-passive
- httpheaders
- securityheaders-cli
- csp-evaluator
- cookie-analyzer

E. TLS/SSL/cert fingerprint
- tlsx
- testssl
- sslscan
- sslyze
- zgrab2
- zlint
- cfssl
- certigo
- certgraph
- ja3
- jarm
- tls-scan
- tls-map
- ctfr
- certstream
- censys-cert-search
- crtsh-cert-search

F. URL archive & historical recon
- waybackurls
- gau
- gauplus
- waymore
- waybackpack
- wayback-machine-downloader
- waybackrobots
- hakrawler-archive
- urlfinder
- getallurls
- paramspider
- urlhunter
- urlgrab
- commoncrawl-index
- alienvault-otx
- urlscan-search
- threatminer
- threatcrowd
- virustotal-domain
- threatfox
- phishtank-url
- urlhaus

G. Crawling & content discovery
- katana
- hakrawler
- gospider
- cariddi
- photon
- crawlergo
- colly-crawler
- playwright-crawler
- scrapy
- linkfinder-crawl
- feroxbuster
- gobuster
- ffuf
- dirsearch
- dirb
- wfuzz
- dirhunt
- rustbuster
- recursivebuster
- raft-wordlist
- seclists-small
- robots-parser
- sitemap-parser
- common-safe-paths

Rules:
- Content discovery tools hanya boleh low-rate.
- Default wordlist kecil.
- Tidak aggressive bruteforce.
- ffuf/gobuster/feroxbuster/dirsearch disabled_by_default untuk profile budget, aktif low-rate untuk recon-only/default jika policy mengizinkan.

H. JavaScript/API recon
- LinkFinder
- xnLinkFinder
- getJS
- subjs
- jsluice
- jsfinder
- jsbeautifier
- sourcemapper
- sourcemap
- webpack-analyzer
- js-endpoint-extractor
- js-secret-patterns
- SecretFinder
- trufflehog
- gitleaks
- detect-secrets
- git-secrets
- semgrep-secrets
- shhgit
- git-hound
- ripgrep-js
- npm-lock-analyzer
- yarn-lock-analyzer
- package-json-analyzer
- retirejs
- osv-scanner
- npm-audit-parser

Rules:
- Secret candidate hanya dicatat sebagai candidate.
- Jangan validasi secret.
- Jangan pakai token/key.
- Jangan print full secret ke log/report. Masking wajib.

I. Parameter & endpoint intelligence
- arjun
- paramspider
- parameth
- x8
- unfurl
- uro
- anew
- qsreplace
- gf
- gf-patterns
- urless
- urldedupe
- urlinsane
- kxss-passive
- dalfox-pipe-passive
- qsreplace-passive
- urlgrep
- endpoint-classifier
- form-parser
- openapi-parser
- swagger-parser
- postman-collection-parser
- graphql-introspect-safe
- graphw00f
- graphql-schema-detector

Rules:
- Tidak melakukan exploit validation.
- Tools seperti kxss/dalfox hanya passive/reflection clue mode, bukan exploit mode.

J. Cloud/storage/CDN recon
- cloudlist
- cloud_enum
- s3scanner
- s3tk
- bucket-stream
- lazys3
- gcpbucketbrute
- azure-blob-enum
- grayhatwarfare-api
- aws-cli-head-only
- gcloud-storage-head-only
- az-storage-head-only
- cloudfront-check
- firebase-scanner
- firebase-hosting-check
- supabase-url-check
- vercel-project-check
- netlify-check
- heroku-domain-check
- render-domain-check
- railway-domain-check
- digitalocean-spaces-check
- doctl-domain-check
- cloudflare-cli
- fastly-cli
- akamai-cli

Rules:
- Hanya metadata/head check.
- Jangan list/download bucket data.
- Bucket brute tools disabled_by_default kecuali explicit authorized.

K. Screenshot & visual recon
- gowitness
- aquatone
- eyewitness
- webscreenshot
- httpscreenshot
- witnessme
- pageres-cli
- playwright-screenshot
- chromium-screenshot
- url-to-image
- screenshot-cluster
- perceptual-hash
- image-dedupe
- login-page-detector
- admin-panel-detector
- default-page-detector

L. Port/service discovery ringan
- naabu
- nmap
- rustscan
- sx
- zgrab2
- nrich
- masscan
- zmap
- unicornscan
- hping3

Rules:
- Default hanya safe common ports.
- masscan/zmap/unicornscan disabled_by_default.
- Tidak scan internet luas.
- Tidak aggressive timing.
- Rate-limit wajib.

M. API/schema/docs discovery
- swagger-ui-detector
- openapi-detector
- redoc-detector
- postman-detector
- graphql-endpoint-detector
- graphiql-detector
- grpcurl-safe
- kiterunner-safe
- soapui-cli-parser
- wsdl-parser
- asyncapi-parser
- api-endpoint-classifier
- rest-route-extractor
- websocket-endpoint-detector
- socketio-detector

N. Mobile/client static recon
- apktool
- jadx
- apkid
- apkleaks
- mobsfscan
- mobsf-static
- strings
- class-dump
- ipa-analyzer
- plist-parser
- android-manifest-parser
- assetlinks-parser
- apple-app-site-association-parser
- deeplink-parser
- firebase-config-parser

Rules:
- Static analysis only.
- Jangan dynamic instrumentation di Fase 5.

O. Repo/public code intelligence
- gitleaks
- trufflehog
- detect-secrets
- git-secrets
- shhgit
- git-hound
- semgrep
- ripgrep
- grep
- repo-supervisor
- github-code-search
- gitlab-search
- bitbucket-search
- sourcegraph-search
- publicwww
- grep-app
- libraries-io
- deps-dev
- osv-scanner
- snyk-cli-passive

P. Container/k8s/config exposure recon
- kube-score
- kubeaudit
- kube-bench
- kube-hunter-passive
- dockerfile-lint
- hadolint
- trivy-config
- checkov
- terrascan
- tfsec
- kube-linter
- helm-lint
- conftest
- regclient
- crane
- grype
- syft

Rules:
- Hanya jika user menyediakan config/image metadata.
- Jangan scan cluster tanpa izin.

Q. Passive intel/API enrichment
- shodan
- censys
- securitytrails
- virustotal
- urlscan
- netlas
- binaryedge
- zoomeye
- fofa
- hunterio
- threatfox
- otx
- threatminer
- fullhunt
- leakix
- publicwww
- builtwith
- crtsh-api
- certspotter-api
- commoncrawl
- github-api
- gitlab-api

Rules:
- API key via vault.
- Jika API key tidak ada, skip gracefully.

4. Tool Availability Check

Implement:

wth recon tools list
wth recon tools available
wth recon tools missing
wth recon tools show <name>
wth recon tools doctor
wth recon tools enable <name>
wth recon tools disable <name>

Output contoh:
- subfinder [available] category=subdomain enabled=true
- amass [missing] category=subdomain enabled=false install_hint="..."
- masscan [available] risk=high enabled=false reason="disabled_by_default"

5. Tool Planner

Buat ReconPlanner yang memilih tools berdasarkan:
- target type: domain, URL, IP, local
- profile: budget/default/recon-only/high-quality/local
- installed tools
- API keys tersedia
- policy allowlist
- rate limit
- risk_level
- enabled_by_default
- job metadata
- realtime mode on/off

Planner tidak boleh menjalankan semua 200 tools sekaligus.

Default plan:
- budget: builtin + 5-10 safe tools
- recon-only: 15-30 safe tools
- default: 20-40 tools
- high-quality: 40-70 tools, tetap rate-limited
- local: boleh private IP/local service kalau policy mengizinkan

Output:
recon_plan.json berisi daftar tools yang akan dijalankan, skipped tools, alasan skip.

6. Tool Runner

Buat safe subprocess runner:
- no shell=True
- args list only
- timeout wajib
- rate limit
- cwd isolated
- env sanitized
- secret redaction
- stdout/stderr max size
- exit code capture
- JSONL log
- artifact output per tool
- never print API keys/tokens
- command injection safe

Output setiap tool:
{
  "tool": "...",
  "status": "success|skipped|failed|timeout",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 0,
  "output_path": "...",
  "error": null,
  "normalized_items": []
}

7. Builtin Recon Modules

Walaupun external tools tidak terinstall, recon tetap harus jalan pakai builtin Python modules:

- builtin DNS resolver
- builtin HTTP probe
- builtin header/cookie parser
- builtin TLS basic cert check
- builtin robots/sitemap fetch
- builtin simple crawler
- builtin JS endpoint extractor
- builtin parameter extractor
- builtin cloud candidate detector
- builtin scoring
- builtin markdown report

Jadi fresh install tetap fungsional.

8. Recon Pipeline Realtime

Implement continuous/diff mode:

wth recon watch <target> --interval 6h --profile recon-only
wth recon diff <old_job_id> <new_job_id>
wth recon changes <target>

Realtime bukan spam request. Default:
- interval minimal 1h
- ada jitter
- rate-limited
- compare hasil scan sebelumnya
- publish event kalau ada perubahan

Change detection:
- new subdomain
- new alive URL
- new port/service
- new endpoint
- new JS file
- new parameter
- new header posture change
- new TLS cert change
- new cloud candidate
- new high-interest target

Event:
- recon.change_detected
- recon.new_asset
- recon.new_endpoint
- recon.report_ready

Tambahkan event types jika perlu, tapi jangan rusak event lama.

9. Evidence folder

Untuk setiap job:

~/.wth-ai/wth-results/<job_id>/
  recon_plan.json
  tool_runs.jsonl
  raw/
    <tool-name>.txt/json
  normalized/
    dns_records.json
    subdomains.json
    alive_urls.json
    headers_cookies.json
    tls.json
    fingerprint.json
    discovered_paths.json
    crawl_urls.json
    js_intel.json
    params.json
    cloud_candidates.json
    priority_targets.json
    asset_graph.json
  screenshots/
  recon_report.md
  recon_summary.json

10. Result Normalizer

Semua output tools harus dinormalisasi ke model internal:
- DomainAsset
- DNSRecord
- URLAsset
- HTTPProbe
- HeaderPosture
- CookiePosture
- TLSInfo
- TechnologyFingerprint
- Endpoint
- Parameter
- JSIntel
- SecretCandidateMasked
- CloudCandidate
- FindingCandidate
- PriorityTarget

Deduplicate wajib.
Confidence score wajib.
Source/tool attribution wajib.

11. Scoring

Interest scoring:
- admin/dev/staging keyword
- auth surface
- interesting parameter
- exposed docs/swagger
- weak/missing headers
- interesting HTTP status
- JS endpoint
- JS secret candidate masked
- cloud candidate
- outdated tech candidate
- new asset/change detected
- multiple tools confirm same asset
- screenshot cluster clue

Output:
priority_targets.json
priority_targets.csv

12. Job Integration

Update jobs/handlers.py:
- recon job pakai ReconEngine nyata
- progress:
  5% scope/policy
  10% tool plan
  20% DNS/subdomain/passive
  35% HTTP/service
  50% headers/TLS/fingerprint
  65% content/crawl
  75% JS/params/API
  85% cloud/enrichment
  92% normalize/scoring/diff
  100% report
- append log per fase
- publish job.progress
- publish report.ready
- publish recon.change_detected kalau watch/diff menemukan perubahan
- save result JSON ke job store

13. CLI Commands

Tambahkan:

wth recon run <target> --profile recon-only
wth recon quick <target>
wth recon watch <target> --interval 6h
wth recon diff <old_job_id> <new_job_id>
wth recon changes <target>
wth recon show <job_id>
wth recon report <job_id>
wth recon tools list
wth recon tools available
wth recon tools missing
wth recon tools doctor
wth recon tools show <name>
wth recon plan <target> --profile recon-only

Important:
- wth recon run/quick/watch harus wrapper ke Job Manager.
- Jangan bypass job system.

14. Doctor Update

wth doctor cek:
- recon module importable
- tool registry len >= 200
- builtin recon works
- result dir writable
- default wordlist tersedia
- DNS resolver tersedia
- HTTP client dependency tersedia
- report writer bisa menulis
- profile recon-only valid
- local policy valid
- external tools availability tidak crash
- missing tools hanya warning, bukan failure

15. Tests

Tambahkan tests tanpa bergantung internet:
- registry minimal 200 tools
- tool availability check mock
- planner budget/default/high-quality memilih tool berbeda
- disabled high-risk tools tidak dipilih default
- runner no shell=True
- runner timeout
- runner redacts secrets
- builtin DNS mock
- builtin HTTP mock
- header parser
- cookie parser
- TLS parser mock
- JS endpoint extraction
- JS secret candidate masked
- parameter extraction
- cloud candidate extraction
- normalizer dedupe/confidence
- scoring output
- report markdown dibuat
- recon plan file dibuat
- tool_runs.jsonl dibuat
- recon job handler completed
- progress events emitted
- report.ready emitted
- diff detects new URL
- watch mode creates next job or stores watch config
- no secret plaintext in logs/result/report/fixture
- doctor detects registry broken
- doctor warns missing external tools, not fail

16. Acceptance Criteria

- `wth job create recon --target example.com` tidak mock lagi.
- Worker menjalankan real recon engine.
- Registry punya minimal 200 ToolSpec.
- Fresh install tetap bisa recon pakai builtin modules.
- External tools kalau tidak ada tidak bikin crash.
- `wth recon tools list` menampilkan 200+ tools.
- `wth recon plan example.com --profile recon-only` menghasilkan plan aman.
- `wth recon quick example.com` membuat job, menjalankan recon, dan membuat hasil.
- Output dibuat di wth-results/<job_id>/.
- recon_report.md dibuat.
- priority_targets.json dibuat.
- recon_plan.json dibuat.
- tool_runs.jsonl dibuat.
- Job progress 0-100.
- Event bus menerima job.progress dan report.ready.
- Telegram/Discord notification tetap jalan via event bus jika enabled.
- Doctor mengecek recon engine + registry.
- Semua test lama tetap hijau.
- Test baru hijau.
- Tidak ada secret plaintext di config/log/report/result/test fixture.

17. Batasan

Jangan implement:
- executor vulnerability validation
- exploit payload
- aggressive fuzzing
- login brute force
- OTP/captcha bypass
- data exfiltration
- dashboard
- distributed worker
- updater/license

18. Ringkasan akhir yang harus diberikan

Setelah selesai, laporkan:
- file baru/diubah
- jumlah ToolSpec di registry
- kategori tools
- tools builtin yang runnable
- tools external yang supported
- command baru
- contoh output `wth recon tools list`
- contoh output `wth recon plan`
- contoh output `wth recon quick`
- struktur wth-results/<job_id>
- acceptance criteria
- hasil pytest
