"""External tool adapters: argv builders + stdout parsers.

Only a curated set of safe, passive external tools have adapters. Every adapter
builds an argv **list** (never a shell string) and parses line-oriented output
into normalized items. Tools without an adapter are reported as ``manual``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse


@dataclass
class Adapter:
    build_args: Callable[[str], list[str]]
    parse: Callable[[str], list[dict[str, Any]]]


def _lines(out: str) -> list[str]:
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]


# --- parsers ---
def _parse_subdomains(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        host = ln.split()[0].lstrip("*.").lower()
        if "." in host:
            items.append({"kind": "subdomain", "host": host})
    return items


def _parse_urls(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        url = ln.split()[0]
        if url.startswith("http"):
            items.append({"kind": "url", "url": url})
    return items


def _parse_httpx(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        url = ln.split()[0]
        if url.startswith("http"):
            items.append({"kind": "url", "url": url})
            items.append({"kind": "http_probe", "url": url, "status": 0})
    return items


def _parse_dnsx(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        host = ln.split()[0].lower()
        if "." in host:
            items.append({"kind": "subdomain", "host": host})
    return items


def _parse_naabu(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        # host:port
        if ":" in ln:
            host, _, port = ln.partition(":")
            items.append(
                {
                    "kind": "finding",
                    "title": f"Open port {port}",
                    "asset": host,
                    "category": "service",
                    "severity_hint": "info",
                    "detail": f"port {port} open (passive light scan)",
                }
            )
    return items


def _host(target: str) -> str:
    if "://" in target:
        return urlparse(target).hostname or target
    return target.split("/")[0]


def _parse_dns_records(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        parts = ln.split()
        if len(parts) >= 2 and parts[0].lower() in ("a", "aaaa", "cname", "mx", "txt", "ns"):
            items.append({"kind": "dns", "record_type": parts[0].upper(), "value": " ".join(parts[1:])})
        elif "." in ln and not ln.startswith(";"):
            host = parts[-1].rstrip(".").lower()
            if "." in host:
                items.append({"kind": "subdomain", "host": host})
    return items


def _parse_whois(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        low = ln.lower()
        if "domain name:" in low or "name server:" in low:
            val = ln.split(":", 1)[-1].strip().lower().rstrip(".")
            if "." in val:
                items.append({"kind": "subdomain", "host": val})
    return items


# --- registry of adapters ---
ADAPTERS: dict[str, Adapter] = {
    "subfinder": Adapter(lambda t: ["subfinder", "-silent", "-d", _host(t)], _parse_subdomains),
    "assetfinder": Adapter(lambda t: ["assetfinder", "--subs-only", _host(t)], _parse_subdomains),
    "findomain": Adapter(lambda t: ["findomain", "-q", "-t", _host(t)], _parse_subdomains),
    "amass": Adapter(lambda t: ["amass", "enum", "-passive", "-d", _host(t)], _parse_subdomains),
    "theHarvester": Adapter(lambda t: ["theHarvester", "-d", _host(t), "-b", "crtsh"], _parse_subdomains),
    "dnsx": Adapter(lambda t: ["dnsx", "-silent", "-d", _host(t)], _parse_dnsx),
    "massdns": Adapter(lambda t: ["massdns", "-r", "/etc/hosts", "-t", "A", _host(t)], _parse_dnsx),
    "puredns": Adapter(lambda t: ["puredns", "resolve", _host(t)], _parse_dnsx),
    "shuffledns": Adapter(lambda t: ["shuffledns", "-d", _host(t)], _parse_dnsx),
    "dnsrecon": Adapter(lambda t: ["dnsrecon", "-d", _host(t), "-t", "std"], _parse_dnsx),
    "httpx": Adapter(lambda t: ["httpx", "-silent", "-u", t], _parse_httpx),
    "httprobe": Adapter(lambda t: ["httprobe"], _parse_urls),
    "whatweb": Adapter(lambda t: ["whatweb", "-a", "1", t], _parse_httpx),
    "wafw00f": Adapter(lambda t: ["wafw00f", t], _parse_httpx),
    "webanalyze": Adapter(lambda t: ["webanalyze", "-host", t], _parse_httpx),
    "tlsx": Adapter(lambda t: ["tlsx", "-u", t, "-silent"], _parse_httpx),
    "testssl": Adapter(lambda t: ["testssl", "--quiet", t], _parse_httpx),
    "securityheaders-cli": Adapter(lambda t: ["securityheaders", t], _parse_httpx),
    "waybackurls": Adapter(lambda t: ["waybackurls", _host(t)], _parse_urls),
    "gau": Adapter(lambda t: ["gau", "--subs", _host(t)], _parse_urls),
    "paramspider": Adapter(lambda t: ["paramspider", "-d", _host(t)], _parse_urls),
    "katana": Adapter(lambda t: ["katana", "-silent", "-u", t], _parse_urls),
    "hakrawler": Adapter(lambda t: ["hakrawler", "-u", t], _parse_urls),
    "arjun": Adapter(lambda t: ["arjun", "-u", t, "--stable"], _parse_urls),
    "feroxbuster": Adapter(lambda t: ["feroxbuster", "-u", t, "-w", "/usr/share/wordlists/dirb/common.txt", "-t", "5", "-q"], _parse_urls),
    "gobuster": Adapter(lambda t: ["gobuster", "dir", "-u", t, "-w", "/usr/share/wordlists/dirb/common.txt", "-q"], _parse_urls),
    "dirsearch": Adapter(lambda t: ["dirsearch", "-u", t, "-w", "/usr/share/wordlists/dirb/common.txt", "--quiet"], _parse_urls),
    "naabu": Adapter(lambda t: ["naabu", "-silent", "-host", _host(t), "-top-ports", "100"], _parse_naabu),
    "nmap": Adapter(lambda t: ["nmap", "-Pn", "-F", _host(t)], _parse_naabu),
    "rustscan": Adapter(lambda t: ["rustscan", "-a", _host(t), "--", "-F"], _parse_naabu),
    "sx": Adapter(lambda t: ["sx", "arp", _host(t)], _parse_naabu),
    "ffuf": Adapter(
        lambda t: ["ffuf", "-u", f"{t.rstrip('/')}/FUZZ", "-w", "/usr/share/wordlists/dirb/common.txt", "-t", "5", "-s"],
        _parse_urls,
    ),
    "dirb": Adapter(lambda t: ["dirb", t, "/usr/share/wordlists/dirb/common.txt", "-S"], _parse_urls),
    "wfuzz": Adapter(
        lambda t: ["wfuzz", "-c", "-z", "file,/usr/share/wordlists/dirb/common.txt", "--hc", "404", t],
        _parse_urls,
    ),
    "dig": Adapter(lambda t: ["dig", "+short", _host(t)], _parse_dns_records),
    "host": Adapter(lambda t: ["host", "-a", _host(t)], _parse_dns_records),
    "whois": Adapter(lambda t: ["whois", _host(t)], _parse_whois),
    "chaos-client": Adapter(lambda t: ["chaos", "-d", _host(t), "-silent"], _parse_subdomains),
    "github-subdomains": Adapter(lambda t: ["github-subdomains", "-d", _host(t)], _parse_subdomains),
    "sublist3r": Adapter(lambda t: ["sublist3r", "-d", _host(t), "-n"], _parse_subdomains),
    "knockpy": Adapter(lambda t: ["knockpy", _host(t)], _parse_subdomains),
    "fierce": Adapter(lambda t: ["fierce", "-dns", _host(t)], _parse_subdomains),
    "dnsenum": Adapter(lambda t: ["dnsenum", _host(t)], _parse_dns_records),
    "dnsvalidator": Adapter(lambda t: ["dnsvalidator", "-t", _host(t)], _parse_dnsx),
    "zdns": Adapter(lambda t: ["zdns", "A", _host(t)], _parse_dns_records),
    "gauplus": Adapter(lambda t: ["gauplus", _host(t)], _parse_urls),
    "waymore": Adapter(lambda t: ["waymore", "-i", _host(t), "-mode", "U"], _parse_urls),
    "gospider": Adapter(lambda t: ["gospider", "-s", t, "-q"], _parse_urls),
    "cariddi": Adapter(lambda t: ["cariddi", "-u", t, "-s"], _parse_urls),
    "photon": Adapter(lambda t: ["photon", "-u", t, "-q"], _parse_urls),
    "sslyze": Adapter(lambda t: ["sslyze", "--regular", t], _parse_httpx),
    "sslscan": Adapter(lambda t: ["sslscan", t], _parse_httpx),
    "jarm": Adapter(lambda t: ["jarm", t], _parse_httpx),
    "retire": Adapter(lambda t: ["retire", "--jspath", t], _parse_httpx),
    "retirejs": Adapter(lambda t: ["retire", "--jspath", t], _parse_httpx),
    "nrich": Adapter(lambda t: ["nrich", _host(t)], _parse_naabu),
    "linkfinder": Adapter(lambda t: ["linkfinder", "-i", t, "-o", "cli"], _parse_urls),
}


def get_adapter(name: str) -> Adapter | None:
    return ADAPTERS.get(name)
