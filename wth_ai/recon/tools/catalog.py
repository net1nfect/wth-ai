"""Static catalog of 200+ recon ToolSpec entries (Fase 5).

The catalog is organized by the categories A-Q from the platform spec. Every
entry is a ``ToolSpec``. Most tools are ``external`` (run via PATH if installed)
or ``api`` (need a vault key, skipped gracefully when absent); a small core set
is ``builtin`` (pure-Python, always runnable on a fresh install).

Safety defaults:
- High-risk tools (mass scanners, bucket brute-force, content fuzzers) are
  ``risk_level != "safe"`` and ``enabled_by_default=False`` so the planner never
  auto-selects them. They remain in the registry and can be enabled explicitly.
- Anything that would exploit / fuzz aggressively is ``stub``/``manual`` so it is
  cataloged but not runnable from the recon engine.
"""

from __future__ import annotations

from typing import Any

from wth_ai.recon.model import ToolSpec

CATEGORY_LABELS: dict[str, str] = {
    "core": "A. Core system/web utilities",
    "subdomain": "B. Subdomain & asset discovery",
    "dns": "C. DNS validation & permutation",
    "http_probe": "D. HTTP probing & web metadata",
    "tls": "E. TLS/SSL/cert fingerprint",
    "archive": "F. URL archive & historical recon",
    "crawl": "G. Crawling & content discovery",
    "js": "H. JavaScript/API recon",
    "param": "I. Parameter & endpoint intelligence",
    "cloud": "J. Cloud/storage/CDN recon",
    "screenshot": "K. Screenshot & visual recon",
    "port": "L. Port/service discovery (light)",
    "api_schema": "M. API/schema/docs discovery",
    "mobile": "N. Mobile/client static recon",
    "code_intel": "O. Repo/public code intelligence",
    "container": "P. Container/k8s/config exposure recon",
    "intel": "Q. Passive intel/API enrichment",
}

# Builtin tools wired to pure-Python implementations in ``builtin.py``.
# (name -> builtin function key). Always runnable on a fresh install.
BUILTIN_TOOLS: dict[str, str] = {
    "python-dnspython": "dns",
    "python-requests": "http",
    "httpheaders": "headers",
    "python-ssl": "tls",
    "robots-parser": "robots",
    "python-beautifulsoup": "crawler",
    "js-endpoint-extractor": "js",
    "endpoint-classifier": "params",
    "cloudfront-check": "cloud",
}

# A curated set of external tools we have real adapters for (argv + parser).
SUPPORTED_EXTERNAL: frozenset[str] = frozenset(
    {
        "subfinder", "assetfinder", "findomain", "dnsx", "httpx", "httprobe",
        "waybackurls", "gau", "katana", "hakrawler", "naabu",
        "amass", "theHarvester", "whatweb", "wafw00f", "tlsx", "testssl",
        "securityheaders-cli", "feroxbuster", "gobuster", "dirsearch",
        "nmap", "massdns", "puredns", "shuffledns", "dnsrecon",
        "webanalyze", "paramspider", "arjun",
        "rustscan", "sx", "ffuf", "dirb", "wfuzz", "dig", "host", "whois",
        "chaos-client", "github-subdomains", "sublist3r", "knockpy", "fierce",
        "dnsenum", "dnsvalidator", "zdns", "gauplus", "waymore", "gospider",
        "cariddi", "photon", "sslyze", "sslscan", "jarm", "retire", "retirejs",
        "nrich", "linkfinder",
    }
)

# High-risk tools: cataloged but disabled by default and never auto-run safely.
_HIGH_RISK: dict[str, str] = {
    "masscan": "high", "zmap": "high", "unicornscan": "high", "hping3": "high",
    "s3scanner": "medium", "bucket-stream": "medium", "lazys3": "medium",
    "gcpbucketbrute": "high", "azure-blob-enum": "medium", "s3tk": "medium",
    "ffuf": "medium", "gobuster": "medium", "feroxbuster": "medium",
    "dirsearch": "medium", "dirb": "medium", "wfuzz": "medium",
    "rustbuster": "medium", "recursivebuster": "medium",
    "wpscan-passive": "medium", "nikto-passive": "medium",
    "joomscan-passive": "medium", "droopescan": "medium",
    "dalfox-pipe-passive": "medium", "kxss-passive": "medium",
    "kiterunner-safe": "medium", "kube-hunter-passive": "medium",
}

# Tools that are cataloged but not safe to auto-execute -> stub/manual mode.
_MANUAL_OR_STUB: dict[str, str] = {
    # Mobile/static (N) — operator supplies artifacts.
    "apktool": "manual", "jadx": "manual", "apkid": "manual", "apkleaks": "manual",
    "mobsfscan": "manual", "mobsf-static": "manual", "class-dump": "manual",
    "ipa-analyzer": "manual", "plist-parser": "manual",
    "android-manifest-parser": "manual", "assetlinks-parser": "manual",
    "apple-app-site-association-parser": "manual", "deeplink-parser": "manual",
    "firebase-config-parser": "manual",
    # Container/config (P) — need provided config/image.
    "kube-score": "manual", "kubeaudit": "manual", "kube-bench": "manual",
    "dockerfile-lint": "manual", "hadolint": "manual", "trivy-config": "manual",
    "checkov": "manual", "terrascan": "manual", "tfsec": "manual",
    "kube-linter": "manual", "helm-lint": "manual", "conftest": "manual",
    "regclient": "manual", "crane": "manual", "grype": "manual", "syft": "manual",
}

# Intel/API providers (Q + *-api/*-cli) require a vault key.
_API_TOOLS: dict[str, str] = {
    "shodan": "shodan", "shodan-cli": "shodan", "censys": "censys",
    "censys-cli": "censys", "securitytrails": "securitytrails",
    "securitytrails-cli": "securitytrails", "virustotal": "virustotal",
    "virustotal-domain": "virustotal", "urlscan-cli": "urlscan",
    "urlscan-search": "urlscan", "netlas": "netlas", "binaryedge": "binaryedge",
    "binaryedge-cli": "binaryedge", "zoomeye": "zoomeye", "zoomeye-cli": "zoomeye",
    "fofa": "fofa", "fofa-cli": "fofa", "hunter": "hunterio", "hunterio": "hunterio",
    "fullhunt": "fullhunt", "leakix": "leakix", "chaos-client": "chaos",
    "github-subdomains": "github", "gitlab-subdomains": "gitlab",
    "github-api": "github", "gitlab-api": "gitlab", "github-code-search": "github",
    "gitlab-search": "gitlab", "grayhatwarfare-api": "grayhatwarfare",
    "haktrails": "securitytrails", "mxtoolbox-api": "mxtoolbox",
    "snyk-cli-passive": "snyk", "cloudflare-cli": "cloudflare",
}

# --------------------------------------------------------------------------- #
# Raw category -> tool-name lists (from the platform spec).
# --------------------------------------------------------------------------- #
_RAW: dict[str, list[str]] = {
    "core": [
        "curl", "httpie", "wget", "dig", "host", "nslookup", "whois", "openssl",
        "jq", "rg", "grep", "python-requests", "python-dnspython", "python-httpx",
        "python-ssl", "python-beautifulsoup", "python-lxml", "playwright",
        "chromium", "sqlite-utils", "yq",
    ],
    "subdomain": [
        "subfinder", "amass", "assetfinder", "findomain", "sublist3r", "knockpy",
        "github-subdomains", "gitlab-subdomains", "chaos-client", "crobat", "cero",
        "haktrails", "theHarvester", "metabigor", "crtsh", "certspotter",
        "censys-subdomain-finder", "rapiddns", "riddler", "sonar", "dnsdumpster",
        "netlas", "shodan-cli", "censys-cli", "securitytrails-cli",
        "binaryedge-cli", "zoomeye-cli", "fofa-cli", "hunter", "urlscan-cli",
    ],
    "dns": [
        "dnsx", "puredns", "shuffledns", "massdns", "zdns", "dnsvalidator",
        "dnsrecon", "dnsenum", "fierce", "dnsmap", "dnsgen", "altdns", "gotator",
        "goaltdns", "ripgen", "dnsprobe", "dnspeep", "dnswalk", "dnstracer",
        "dnsviz", "checkdmarc", "dmarc-cat", "spoofy", "mailspoof", "mxtoolbox-api",
        "spfquery", "dkimsign", "dkimpy",
    ],
    "http_probe": [
        "httpx", "httprobe", "hakcheckurl", "meg", "xurlfind3r", "httping",
        "htrace", "webanalyze", "whatweb", "wappalyzer", "wafw00f", "fav-up",
        "faviconhash", "mmh3", "retire", "retirejs", "builtwith-cli", "cmseek",
        "droopescan", "wpscan-passive", "joomscan-passive", "nikto-passive",
        "httpheaders", "securityheaders-cli", "csp-evaluator", "cookie-analyzer",
    ],
    "tls": [
        "tlsx", "testssl", "sslscan", "sslyze", "zgrab2", "zlint", "cfssl",
        "certigo", "certgraph", "ja3", "jarm", "tls-scan", "tls-map", "ctfr",
        "certstream", "censys-cert-search", "crtsh-cert-search", "python-ssl",
    ],
    "archive": [
        "waybackurls", "gau", "gauplus", "waymore", "waybackpack",
        "wayback-machine-downloader", "waybackrobots", "hakrawler-archive",
        "urlfinder", "getallurls", "paramspider", "urlhunter", "urlgrab",
        "commoncrawl-index", "alienvault-otx", "urlscan-search", "threatminer",
        "threatcrowd", "virustotal-domain", "threatfox", "phishtank-url", "urlhaus",
    ],
    "crawl": [
        "katana", "hakrawler", "gospider", "cariddi", "photon", "crawlergo",
        "colly-crawler", "playwright-crawler", "scrapy", "linkfinder-crawl",
        "feroxbuster", "gobuster", "ffuf", "dirsearch", "dirb", "wfuzz", "dirhunt",
        "rustbuster", "recursivebuster", "raft-wordlist", "seclists-small",
        "robots-parser", "sitemap-parser", "common-safe-paths",
    ],
    "js": [
        "linkfinder", "xnLinkFinder", "getJS", "subjs", "jsluice", "jsfinder",
        "jsbeautifier", "sourcemapper", "sourcemap", "webpack-analyzer",
        "js-endpoint-extractor", "js-secret-patterns", "SecretFinder", "trufflehog",
        "gitleaks", "detect-secrets", "git-secrets", "semgrep-secrets", "shhgit",
        "git-hound", "ripgrep-js", "npm-lock-analyzer", "yarn-lock-analyzer",
        "package-json-analyzer", "retirejs", "osv-scanner", "npm-audit-parser",
    ],
    "param": [
        "arjun", "paramspider", "parameth", "x8", "unfurl", "uro", "anew",
        "qsreplace", "gf", "gf-patterns", "urless", "urldedupe", "urlinsane",
        "kxss-passive", "dalfox-pipe-passive", "qsreplace-passive", "urlgrep",
        "endpoint-classifier", "form-parser", "openapi-parser", "swagger-parser",
        "postman-collection-parser", "graphql-introspect-safe", "graphw00f",
        "graphql-schema-detector",
    ],
    "cloud": [
        "cloudlist", "cloud_enum", "s3scanner", "s3tk", "bucket-stream", "lazys3",
        "gcpbucketbrute", "azure-blob-enum", "grayhatwarfare-api",
        "aws-cli-head-only", "gcloud-storage-head-only", "az-storage-head-only",
        "cloudfront-check", "firebase-scanner", "firebase-hosting-check",
        "supabase-url-check", "vercel-project-check", "netlify-check",
        "heroku-domain-check", "render-domain-check", "railway-domain-check",
        "digitalocean-spaces-check", "doctl-domain-check", "cloudflare-cli",
        "fastly-cli", "akamai-cli",
    ],
    "screenshot": [
        "gowitness", "aquatone", "eyewitness", "webscreenshot", "httpscreenshot",
        "witnessme", "pageres-cli", "playwright-screenshot", "chromium-screenshot",
        "url-to-image", "screenshot-cluster", "perceptual-hash", "image-dedupe",
        "login-page-detector", "admin-panel-detector", "default-page-detector",
    ],
    "port": [
        "naabu", "nmap", "rustscan", "sx", "zgrab2", "nrich", "masscan", "zmap",
        "unicornscan", "hping3",
    ],
    "api_schema": [
        "swagger-ui-detector", "openapi-detector", "redoc-detector",
        "postman-detector", "graphql-endpoint-detector", "graphiql-detector",
        "grpcurl-safe", "kiterunner-safe", "soapui-cli-parser", "wsdl-parser",
        "asyncapi-parser", "api-endpoint-classifier", "rest-route-extractor",
        "websocket-endpoint-detector", "socketio-detector",
    ],
    "mobile": [
        "apktool", "jadx", "apkid", "apkleaks", "mobsfscan", "mobsf-static",
        "strings", "class-dump", "ipa-analyzer", "plist-parser",
        "android-manifest-parser", "assetlinks-parser",
        "apple-app-site-association-parser", "deeplink-parser",
        "firebase-config-parser",
    ],
    "code_intel": [
        "gitleaks", "trufflehog", "detect-secrets", "git-secrets", "shhgit",
        "git-hound", "semgrep", "ripgrep", "grep", "repo-supervisor",
        "github-code-search", "gitlab-search", "bitbucket-search",
        "sourcegraph-search", "publicwww", "grep-app", "libraries-io", "deps-dev",
        "osv-scanner", "snyk-cli-passive",
    ],
    "container": [
        "kube-score", "kubeaudit", "kube-bench", "kube-hunter-passive",
        "dockerfile-lint", "hadolint", "trivy-config", "checkov", "terrascan",
        "tfsec", "kube-linter", "helm-lint", "conftest", "regclient", "crane",
        "grype", "syft",
    ],
    "intel": [
        "shodan", "censys", "securitytrails", "virustotal", "urlscan", "netlas",
        "binaryedge", "zoomeye", "fofa", "hunterio", "threatfox", "otx",
        "threatminer", "fullhunt", "leakix", "publicwww", "builtwith", "crtsh-api",
        "certspotter-api", "commoncrawl", "github-api", "gitlab-api",
    ],
}

# Capability + output type defaults per category (advisory).
_CATEGORY_OUTPUT: dict[str, list[str]] = {
    "core": ["raw"],
    "subdomain": ["subdomain"],
    "dns": ["dns_record"],
    "http_probe": ["http_probe", "fingerprint", "header"],
    "tls": ["tls"],
    "archive": ["url"],
    "crawl": ["url", "endpoint"],
    "js": ["js_intel", "secret_candidate", "endpoint"],
    "param": ["parameter", "endpoint"],
    "cloud": ["cloud_candidate"],
    "screenshot": ["screenshot"],
    "port": ["service"],
    "api_schema": ["endpoint"],
    "mobile": ["secret_candidate", "endpoint"],
    "code_intel": ["secret_candidate"],
    "container": ["finding"],
    "intel": ["subdomain", "dns_record", "url"],
}


def _spec_for(name: str, category: str) -> ToolSpec:
    """Build a ToolSpec for ``name`` applying category defaults + overrides."""
    output_types = list(_CATEGORY_OUTPUT.get(category, ["raw"]))
    cap = [f"{category}_recon"]

    # Defaults
    execution_mode = "external"
    risk = "safe"
    enabled = True
    requires_network = True
    requires_api_key = False
    api_key_ref: str | None = None
    allowed = ["recon-only", "default", "high-quality"]
    notes = ""

    if name in BUILTIN_TOOLS:
        execution_mode = "builtin"
        allowed = ["budget", "recon-only", "default", "high-quality", "local"]
        notes = "builtin pure-Python module (always runnable)"
        if name == "python-ssl":
            requires_network = True
    elif name in _API_TOOLS:
        execution_mode = "api"
        requires_api_key = True
        api_key_ref = f"vault:{_API_TOOLS[name]}_api_key"
        notes = "passive API enrichment; skipped gracefully without key"
    elif name in _MANUAL_OR_STUB:
        execution_mode = _MANUAL_OR_STUB[name]
        requires_network = category not in {"mobile", "container"}
        notes = "requires operator-provided artifact; not auto-run"

    if name in _HIGH_RISK:
        risk = _HIGH_RISK[name]
        enabled = False
        notes = (notes + "; " if notes else "") + "disabled_by_default (risk)"
        # budget never gets risky tools; high-quality/recon-only may enable explicitly
        allowed = [p for p in allowed if p != "budget"]

    # budget profile only gets builtin + a few safe passive tools
    if execution_mode != "builtin" and "budget" not in allowed and risk == "safe":
        # allow lightweight passive tools into budget for the core safe set
        if name in SUPPORTED_EXTERNAL or category in {"subdomain", "http_probe", "archive"}:
            allowed = ["budget", *allowed]

    # `max` is the "everything eligible" profile: it grants no permission of its
    # own, it mirrors `high-quality` so a tool that is allowed in high-quality is
    # also allowed under max. Without this, `profile="max"` validated against
    # PROFILE_CAP but every tool was then rejected by the allowed_profiles check,
    # so the profile silently selected nothing.
    if "high-quality" in allowed and "max" not in allowed:
        allowed.append("max")

    return ToolSpec(
        name=name,
        category=category,
        capabilities=cap,
        execution_mode=execution_mode,
        binary_names=[name] if execution_mode == "external" else [],
        enabled_by_default=enabled,
        risk_level=risk,
        requires_network=requires_network,
        requires_api_key=requires_api_key,
        api_key_ref=api_key_ref,
        allowed_profiles=allowed,
        output_types=output_types,
        rate_limited=True,
        notes=notes,
    )


def build_catalog() -> dict[str, ToolSpec]:
    """Return the full {name: ToolSpec} catalog (first occurrence wins)."""
    catalog: dict[str, ToolSpec] = {}
    for category, names in _RAW.items():
        for name in names:
            if name in catalog:
                continue  # cross-category duplicate (e.g. gitleaks) — keep first
            catalog[name] = _spec_for(name, category)
    return catalog
