"""Black-box checks against a running stack through the nginx edge (default http://127.0.0.1:8080).

    python -m scripts.verify_stack [--base-url URL] [--expect-shared-state]

Checks: edge health, API reachability, security headers on every location type, error envelope,
concurrent turns on one conversation, and (with --expect-shared-state) that the IP burst limit is
enforced once across all replicas rather than once per replica. Uses only offline/deterministic
endpoints; it never triggers paid model traffic unless the stack itself has AI enabled.
Exit code 0 = all checks passed.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import sys
import time

import httpx

GOA = "hotel-goa-001"
REQUIRED_HEADERS = {
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=()",
}
results: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> None:
    results.append((ok, name))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def headers_ok(response: httpx.Response, label: str) -> None:
    for header, expected in REQUIRED_HEADERS.items():
        values = response.headers.get_list(header)
        check(len(values) == 1 and expected in values[0], f"{label}: {header} present once and contains {expected!r}")
    check("strict-transport-security" not in response.headers, f"{label}: no HSTS over plain HTTP (added at the TLS edge)")
    server = response.headers.get("server", "")
    check(not any(ch.isdigit() for ch in server), f"{label}: server header has no version ({server!r})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--expect-shared-state", action="store_true")
    parser.add_argument("--burst-limit", type=int, default=30, help="RATE_LIMIT_IP_BURST of the stack under test")
    parser.add_argument("--burst-window", type=int, default=10, help="RATE_LIMIT_BURST_WINDOW_SECONDS of the stack under test")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    api = f"{base}/api/v1/hotels/{GOA}"

    with httpx.Client(timeout=30) as client:
        check(client.get(f"{base}/healthz").status_code == 200, "edge /healthz")
        index = client.get(f"{base}/")
        check(index.status_code == 200 and "<div id=\"root\">" in index.text, "SPA index served")
        headers_ok(index, "index")
        asset = next((part.split('"')[0] for part in index.text.split('src="')[1:] if part.startswith("/assets/")), None)
        if asset:
            asset_response = client.get(f"{base}{asset}")
            check(asset_response.status_code == 200 and "immutable" in asset_response.headers.get("cache-control", ""), "hashed asset served with immutable caching")
            headers_ok(asset_response, "asset")
        hotel = client.get(api)
        check(hotel.status_code == 200 and hotel.json()["hotel"]["id"] == GOA, "API reachable through the edge")
        headers_ok(hotel, "api")
        check(hotel.headers.get("cache-control") == "no-store", "api: Cache-Control no-store")
        check(bool(hotel.headers.get("x-request-id")), "api: X-Request-ID returned")

        missing = client.post(f"{base}/api/v1/hotels/hotel-none-000/conversations", json={})
        body = missing.json()
        check(missing.status_code == 404 and body["error"]["code"] == "HOTEL_NOT_FOUND" and body["error"]["request_id"], "error envelope with code and request_id")
        too_big = client.post(f"{api}/conversations", content=b"x" * 70_000, headers={"Content-Type": "application/json"})
        check(too_big.status_code == 413, f"oversized body rejected at the edge or API (status {too_big.status_code})")
        metrics = client.get(f"{base}/metrics")
        check("assistant_requests_total" not in metrics.text, "/metrics is not reachable through the edge")

        pause = args.burst_window + 1  # start each phase in a fresh burst window
        time.sleep(pause)
        cid = client.post(f"{api}/conversations", json={}).json()["conversation_id"]
        with ThreadPoolExecutor(8) as pool:
            turns = list(pool.map(lambda i: client.post(f"{api}/conversations/{cid}/messages", json={"message": f"What time is check-in? ({i})"}), range(8)))
        statuses = [t.status_code for t in turns]
        answered = statuses.count(200)
        check(set(statuses) <= {200, 409, 429}, f"concurrent turns return 200/409/429 only ({statuses})")
        time.sleep(pause)
        view = client.get(f"{api}/conversations/{cid}").json()
        check(len(view["messages"]) == 2 * answered, f"no lost updates: {len(view['messages'])} stored messages for {answered} answered turns")

        if args.expect_shared_state:
            time.sleep(pause)
            attempts = 2 * args.burst_limit
            with ThreadPoolExecutor(10) as pool:
                burst = list(pool.map(lambda _: client.get(api).status_code, range(attempts)))
            allowed = burst.count(200)
            check(
                allowed == args.burst_limit and burst.count(429) == attempts - args.burst_limit,
                f"IP burst limit shared across replicas: {allowed} allowed of {attempts} (limit {args.burst_limit} per {args.burst_window} s)",
            )

    failed = [name for ok, name in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
