"""Tech-specific planning profiles — v5 expert (10 profiles)."""

from __future__ import annotations

from typing import Any


class PlanningProfile:
    name: str = "generic"
    default_chains: list[str] = []

    def extra_context(self, memory: Any) -> str:
        return ""

    def default_tasks(self, target: str) -> list[dict[str, Any]]:
        return []


class LaravelProfile(PlanningProfile):
    name = "laravel"
    default_chains = ["idor", "auth", "xss"]

    def extra_context(self, memory) -> str:
        return """
LARAVEL — route+ID BOLA, Policy gaps, .env exposure, CSRF on web routes, mass assignment on API.
Grep: Route::, middleware auth, $request->all(), Policy::
"""


class ApiProfile(PlanningProfile):
    name = "api"
    default_chains = ["idor", "auth_jwt", "ssrf"]

    def extra_context(self, memory) -> str:
        return """
API — BOLA on object IDs, JWT alg none, mass assignment, rate limits, GraphQL introspection.
"""


class GenericWebProfile(PlanningProfile):
    name = "generic_web"
    default_chains = ["xss", "ssrf", "idor"]

    def extra_context(self, memory) -> str:
        return "GENERIC WEB — reflected XSS, open redirect, IDOR on ?id=, auth boundaries."


class WordPressProfile(PlanningProfile):
    name = "wordpress"
    default_chains = ["xss", "auth"]

    def extra_context(self, memory) -> str:
        return "WORDPRESS — plugin CVEs, wp-json REST, user enum /wp-json/wp/v2/users, xmlrpc."


class DjangoProfile(PlanningProfile):
    name = "django"
    default_chains = ["idor", "ssrf"]

    def extra_context(self, memory) -> str:
        return "DJANGO — DEBUG leak, admin /admin/, IDOR on /api/, SSRF in fetch features."


class RailsProfile(PlanningProfile):
    name = "rails"
    default_chains = ["idor", "auth"]

    def extra_context(self, memory) -> str:
        return "RAILS — mass assignment, debug pages, ActiveStorage IDOR, JWT/session fixation."


class SpringProfile(PlanningProfile):
    name = "spring"
    default_chains = ["ssrf", "auth"]

    def extra_context(self, memory) -> str:
        return "SPRING — actuator /actuator/env, SpEL injection, JWT misconfig, path traversal."


class NextJsProfile(PlanningProfile):
    name = "nextjs"
    default_chains = ["ssrf", "xss"]

    def extra_context(self, memory) -> str:
        return "NEXTJS — SSRF in image optimizer, middleware bypass, API routes BOLA, server actions."


class GraphqlProfile(PlanningProfile):
    name = "graphql"
    default_chains = ["idor", "auth"]

    def extra_context(self, memory) -> str:
        return "GRAPHQL — introspection, batching DoS, BOLA on node IDs, field authz gaps."


class FirebaseProfile(PlanningProfile):
    name = "firebase"
    default_chains = ["idor", "auth"]

    def extra_context(self, memory) -> str:
        return "FIREBASE — open rules, API key abuse, IDOR on Firestore paths, auth bypass."


class ShopifyProfile(PlanningProfile):
    name = "shopify"
    default_chains = ["idor", "race"]

    def extra_context(self, memory) -> str:
        return "SHOPIFY — cart/checkout race, discount abuse, app proxy SSRF, storefront API BOLA."


PROFILES = {
    "laravel": LaravelProfile(),
    "api": ApiProfile(),
    "generic_web": GenericWebProfile(),
    "wordpress": WordPressProfile(),
    "django": DjangoProfile(),
    "rails": RailsProfile(),
    "spring": SpringProfile(),
    "nextjs": NextJsProfile(),
    "graphql": GraphqlProfile(),
    "firebase": FirebaseProfile(),
    "shopify": ShopifyProfile(),
}


def get_profile(name: str) -> PlanningProfile:
    return PROFILES.get(name, PROFILES["generic_web"])
