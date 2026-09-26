#!/usr/bin/env python3
"""
license_check — activate a license, and gate the post tools on it.

    ./license_check.py activate <key>             # a purchase key, or a comp token
    ./license_check.py activate --github          # friends: licensed by GitHub access to the repo
    ./license_check.py status
    ./license_check.py start-project <shoot folder>   # per-project licenses: begins one project
    ./license_check.py deactivate
    ./license_check.py --selftest

## What this is, and what it is not

It is not DRM. Everything here is readable Python, and it has to be: the skill only works because
Claude can read it. The check keeps an honest buyer honest, makes a shared copy traceable to the
key that activated it, and keeps new projects tied to a live license. Nothing more is claimed.

## 🔴 The rule above every other rule: never stand between someone and their footage

Only the post tools call gate(). offload.py, shootcheck.py, offload_selftest.py, the fixtures,
the on-set viewing LUT and the day sheets never do, and no failure in this file may ever reach
them. A licence server that is down, a hotel Wi-Fi that drops, a card being verified at 2am —
none of those may cost anyone a card. `--selftest` is always exempt, so every harness in this
repo runs with no license present.

## Four kinds of license

  purchase, per project   `XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX`  from the one-time product.
                          Each `start-project` uses one; the order's quantity is the allowance.
                          Inside a started project every post tool works fully offline.
  purchase, annual        same shape, from the membership product. One seat per computer
                          (Creator 1, Studio 10). Checked at most once a day; 7 days offline grace.
  comp                    `fscomp1.<payload>.<signature>` — issued by hand to friends, reviewers,
                          collaborators. Verified entirely OFFLINE against the public key below:
                          no network, no store, no expiry unless the token itself carries one.
                          It keeps working if the store, the company and this file's network code
                          all disappear. That is the point of it.
  github                  friends and collaborators with read access to the private film-shoot
                          repo. Nothing to type: if no license is active and this copy is a git
                          checkout, the gate asks GitHub -- with the person's own existing git
                          credentials -- whether they can read the repo, and if so records a
                          friend license. Revocable: remove the collaborator and it stops at the
                          next daily recheck. 30 days offline grace. 🔴 Only honoured while the
                          repo is PRIVATE -- a public repo is readable by everyone, so the check
                          first confirms an anonymous read is refused.

⚠️ A network failure is never read as "invalid". Only an explicit answer from the store is.

## What leaves the machine

Only the license key and a product id, to api.gumroad.com/v2/licenses/verify — an endpoint that
needs no seller credential, so no secret ships in this file. No footage, no file names, no paths,
no machine identifiers. `install_id` is a random UUID that never leaves `license.json`.
The GitHub check is `git ls-remote` against the film-shoot repo, using whatever credentials git
already has -- it sends nothing of ours, reads nothing but the repo's branch list, never prompts.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# ---- store identity -------------------------------------------------------------------------
VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
PRODUCT_PER_PROJECT = "GIYdhvDPNKtl2jb-tKs5RQ=="
PRODUCT_ANNUAL = "rtKaXWC5El-CsLmqDCAVDA=="
BUY_URL = "https://sklar.gumroad.com/l/firsttake"
RENEW_URL = "https://sklar.gumroad.com/l/firsttake-annual"
SUPPORT = "sklarstad@gmail.com"

# Ed25519 public key for comp tokens. The private half is held by the licensor, never in a repo.
COMP_PUBLIC_KEY = bytes.fromhex("417b20c731c9a7d01774d874b63bb60a9971cea5dc91e12b88f8405df163cda6")

SEATS = {"creator": 1, "studio": 10}
REVERIFY_AFTER = _dt.timedelta(hours=24)
OFFLINE_GRACE = _dt.timedelta(days=7)
PROJECT_STAMP = ".film-shoot-project.json"

GITHUB_REPO = "emersonsklar/film-shoot"
GITHUB_GRACE = _dt.timedelta(days=30)
SKILL_ROOT = Path(__file__).resolve().parent.parent


class LicenseError(Exception):
    """A definite 'no', with a message a person can act on."""


class StoreUnreachable(Exception):
    """The store could not be asked. Never treated as a 'no' on its own."""


# ---- where state lives ------------------------------------------------------------------------
def home() -> Path:
    # FILM_SHOOT_HOME exists for the selftest; it matches ~/.film-shoot used by premiere-jobs.
    return Path(os.environ.get("FILM_SHOOT_HOME") or Path.home() / ".film-shoot")


def license_path() -> Path:
    return home() / "license.json"


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(t: _dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def load_license() -> dict | None:
    try:
        return json.loads(license_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_license(lic: dict) -> None:
    """Write atomically: full file to a temp beside it, then one rename.

    ⚠️ A direct `write_text` truncates first and then writes, so a crash, a full disk or a killed
    process leaves a HALF-WRITTEN license.json. `load_license` treats an unparseable file as absent,
    so the usual outcome is a silent re-activation rather than a failure — but the same interruption
    can also leave valid JSON with keys missing, which is the exact record that used to produce a
    traceback. A rename on the same filesystem is atomic, so the file on disk is always either the
    old licence or the new one, never a torn mixture of both.
    """
    p = license_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial")
    tmp.write_text(json.dumps(lic, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


# ---- Ed25519 verification, pure Python (RFC 8032 §5.1.7) ---------------------------------------
# No dependency, because this repo's requirements are python3, ffmpeg and numpy and a licence
# check must not add a fourth. Slow (tens of ms), and called once per tool run — irrelevant.
_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _eq(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(b: bytes):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def ed25519_verify(public: bytes, msg: bytes, sig: bytes) -> bool:
    if len(public) != 32 or len(sig) != 64:
        return False
    A, R = _decompress(public), _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + public + msg).digest(), "little") % _Q
    return _eq(_mul(s, _G), _add(R, _mul(h, A)))


# ---- comp tokens -------------------------------------------------------------------------------
def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def read_comp(token: str, public_key: bytes | None = None) -> dict:
    """Return the comp payload if the signature holds and it has not expired; else LicenseError."""
    public_key = COMP_PUBLIC_KEY if public_key is None else public_key
    try:
        prefix, payload_b64, sig_b64 = token.strip().split(".")
        payload_bytes, sig = _b64d(payload_b64), _b64d(sig_b64)
    except ValueError:
        raise LicenseError("That comp token is incomplete — copy the whole line, from 'fscomp1.' "
                           "to the end.") from None
    if prefix != "fscomp1" or not ed25519_verify(public_key, payload_bytes, sig):
        raise LicenseError(f"That comp token isn't valid. Ask {SUPPORT} to reissue it.")
    payload = json.loads(payload_bytes)
    expires = payload.get("expires")
    if expires and _dt.date.fromisoformat(expires) < now().date():
        raise LicenseError(f"This comp license ended on {expires}. Contact {SUPPORT} to extend it.")
    return payload


# ---- the store ---------------------------------------------------------------------------------
def store_verify(product_id: str, key: str, increment: bool) -> dict:
    body = urllib.parse.urlencode({"product_id": product_id, "license_key": key,
                                   "increment_uses_count": "true" if increment else "false"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(VERIFY_URL, data=body), timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:                       # the store's answer for "no such key"
            return {"success": False}
        raise StoreUnreachable(f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StoreUnreachable(str(e)) from None


# Replaced by the selftest with a fake store.
_verify = store_verify


def judge_purchase(resp: dict, tier: str) -> None:
    """Raise LicenseError for any definite reason this purchase should not start new work."""
    if not resp.get("success"):
        raise LicenseError(f"That license key wasn't recognised, or has been disabled. "
                           f"If you bought one, reply to your receipt or email {SUPPORT}.")
    p = resp.get("purchase", {})
    if p.get("refunded") or p.get("chargebacked") or (p.get("disputed") and not p.get("dispute_won")):
        raise LicenseError(f"The purchase behind this key was refunded or disputed, so it no longer "
                           f"activates. Questions: {SUPPORT}.")
    if tier in SEATS and (p.get("subscription_ended_at") or p.get("subscription_failed_at")):
        raise LicenseError(f"Your annual license has ended. Projects you already finished are "
                           f"yours; to start new work, renew at {RENEW_URL}.")


def tier_of(resp: dict) -> str:
    return "studio" if "studio" in (resp.get("purchase", {}).get("variants") or "").lower() else "creator"


# ---- GitHub access (friends) ---------------------------------------------------------------------
# Checked FIRST: a network failure over ssh also ends in "could not read from remote repository",
# so the network markers must win over anything that looks like a refusal.
_UNREACHABLE = ("could not resolve", "timed out", "connection refused", "network is unreachable",
                "failed to connect", "connection reset", "connection closed", "no route to host")
_DENIED = ("repository not found", "authentication failed", "permission denied",
           "returned error: 401", "returned error: 403", "could not read username",
           "could not read password", "terminal prompts disabled",
           # ssh not set up for GitHub on this machine: a definite no for that route, not a
           # flaky network -- it fails locally before anything is sent.
           "host key verification failed", "no such identity")


def _classify(stderr: str) -> str:
    err = stderr.lower()
    if any(m in err for m in _UNREACHABLE):
        return "unreachable"
    return "denied" if any(m in err for m in _DENIED) else "unreachable"


def _ls_remote(url: str, authenticated: bool) -> str:
    """'ok', 'denied', or 'unreachable'. Never prompts, never hangs past 20 s.

    ⚠️ Anything not recognisably a refusal is 'unreachable', because an unrecognised failure
    must never be read as "no access" -- that would lock a friend out over a flaky network.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10")
    cmd = ["git"]
    if not authenticated:
        cmd += ["-c", "credential.helper=", "-c", "core.askPass="]
        env.pop("GIT_ASKPASS", None)
        env.pop("SSH_ASKPASS", None)
    cmd += ["ls-remote", "--heads", url]
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "unreachable"
    return "ok" if r.returncode == 0 else _classify(r.stderr)


def github_access() -> bool | None:
    """True: this machine's git credentials can read the private repo. False: definitely not,
    or the repo is public (so reading it proves nothing). None: couldn't tell."""
    https = f"https://github.com/{GITHUB_REPO}.git"
    anonymous = _ls_remote(https, authenticated=False)
    if anonymous == "ok":
        return False                       # public: readable by anyone, licenses no one
    if anonymous == "unreachable":
        return None
    urls = []
    try:
        origin = subprocess.run(["git", "-C", str(SKILL_ROOT), "remote", "get-url", "origin"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if GITHUB_REPO in origin:
            urls.append(origin)            # the protocol they already cloned with, first
    except (OSError, subprocess.TimeoutExpired):
        pass
    urls += [https, f"git@github.com:{GITHUB_REPO}.git"]
    results = []
    for url in dict.fromkeys(urls):
        res = _ls_remote(url, authenticated=True)
        if res == "ok":
            return True
        results.append(res)
    return False if all(r == "denied" for r in results) else None


# Replaced by the selftest with a fake.
_github = github_access


def activate_github() -> dict:
    ok = _github()
    if ok is None:
        raise LicenseError("Couldn't reach GitHub to check your access. Try again when you're "
                           "online; nothing else is needed.")
    if not ok:
        raise LicenseError(f"Your GitHub account can't read the film-shoot repo, so there's no "
                           f"friend license to activate. If you should have access, email {SUPPORT}.")
    old = load_license()
    lic = {"key": f"github:{GITHUB_REPO}", "kind": "github", "tier": "friend",
           "install_id": old["install_id"] if old and old.get("kind") == "github" else str(uuid.uuid4()),
           "activated_at": iso(now()), "last_verified_at": iso(now()), "last_verified_ok": True}
    save_license(lic)
    return lic


# ---- commands ----------------------------------------------------------------------------------
def activate(key: str) -> dict:
    key = key.strip()
    if key == "--github":
        return activate_github()
    old = load_license()
    install_id = old["install_id"] if old and old.get("key") == key else str(uuid.uuid4())

    if key.startswith("fscomp1."):
        payload = read_comp(key)
        lic = {"key": key, "kind": "comp", "tier": "comp", "holder": payload.get("name"),
               "install_id": install_id, "activated_at": iso(now())}
        save_license(lic)
        return lic

    try:
        per_project = _verify(PRODUCT_PER_PROJECT, key, increment=False)
        if per_project.get("success"):
            judge_purchase(per_project, "per-project")
            lic = {"key": key, "kind": "purchase", "tier": "per-project",
                   "product_id": PRODUCT_PER_PROJECT, "install_id": install_id,
                   "activated_at": iso(now()), "last_verified_at": iso(now()),
                   "last_verified_ok": True}
            save_license(lic)
            return lic
        annual = _verify(PRODUCT_ANNUAL, key, increment=False)
        tier = tier_of(annual)
        judge_purchase(annual, tier)
        same_install = bool(old and old.get("key") == key)
        if not same_install:
            # One seat is one computer. Re-activating on the same computer costs nothing.
            counted = _verify(PRODUCT_ANNUAL, key, increment=True)
            limit = SEATS[tier] * int(counted.get("purchase", {}).get("quantity") or 1)
            if int(counted.get("uses", 0)) > limit:
                raise LicenseError(
                    f"This key is already active on {limit} computer{'s' if limit > 1 else ''}, "
                    f"which is all its {tier.title()} license covers. To move it to this one, "
                    f"email {SUPPORT} and we'll issue a replacement key.")
    except StoreUnreachable:
        raise LicenseError("Couldn't reach the license server to activate. Activation needs a "
                           "connection once; after that, work continues offline.") from None
    lic = {"key": key, "kind": "purchase", "tier": tier, "product_id": PRODUCT_ANNUAL,
           "install_id": install_id, "activated_at": iso(now()),
           "last_verified_at": iso(now()), "last_verified_ok": True}
    save_license(lic)
    return lic


def start_project(folder: Path) -> dict:
    lic = load_license()
    if not lic:
        raise LicenseError(f"No license is active on this computer. Run "
                           f"`license_check.py activate <key>` — buy one at {BUY_URL}.")
    stamp_path = folder / PROJECT_STAMP
    if stamp_path.is_file():
        return json.loads(stamp_path.read_text(encoding="utf-8"))
    if lic["tier"] == "per-project":
        try:
            resp = _verify(lic["product_id"], lic["key"], increment=True)
        except StoreUnreachable:
            raise LicenseError("Starting a new project needs a connection once, to record it "
                               "against your license. Work inside a started project runs "
                               "offline.") from None
        judge_purchase(resp, "per-project")
        allowed = int(resp.get("purchase", {}).get("quantity") or 1)
        if int(resp.get("uses", 0)) > allowed:
            raise LicenseError(f"Every project on this license has been used ({allowed}). Another "
                               f"project is one more license at {BUY_URL} — or annual, unlimited, "
                               f"at {RENEW_URL}.")
    else:
        check_active(lic)
    stamp = {"project_id": str(uuid.uuid4()), "started_at": iso(now()),
             "key_fingerprint": fingerprint(lic["key"]), "tier": lic["tier"]}
    folder.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    return stamp


def check_active(lic: dict) -> None:
    """Annual and comp: is this license good for work right now? Raises LicenseError if not."""
    if lic["kind"] == "comp":
        read_comp(lic["key"])
        return
    last = parse_iso(lic["last_verified_at"])
    if lic["kind"] == "github":
        if now() - last < REVERIFY_AFTER and lic.get("last_verified_ok"):
            return
        ok = _github()
        if ok is None:
            if lic.get("last_verified_ok") and now() - last < GITHUB_GRACE:
                return
            raise LicenseError("Your friend license hasn't been confirmed with GitHub for over 30 "
                               "days. Connect once and run this again; nothing else is needed.")
        lic["last_verified_ok"], lic["last_verified_at"] = bool(ok), iso(now())
        save_license(lic)
        if not ok:
            raise LicenseError(f"Your GitHub account no longer has access to the film-shoot repo, "
                               f"which is what licensed this computer. If that's a mistake, "
                               f"email {SUPPORT}.")
        return
    if now() - last < REVERIFY_AFTER and lic.get("last_verified_ok"):
        return
    try:
        resp = _verify(lic["product_id"], lic["key"], increment=False)
    except StoreUnreachable:
        if lic.get("last_verified_ok") and now() - last < OFFLINE_GRACE:
            return
        raise LicenseError("Your license hasn't been confirmed for over a week because the "
                           "license server couldn't be reached. Connect once and run this again; "
                           "nothing else is needed.") from None
    try:
        judge_purchase(resp, lic["tier"])
    except LicenseError:
        lic["last_verified_ok"] = False
        lic["last_verified_at"] = iso(now())
        save_license(lic)
        raise
    lic["last_verified_ok"] = True
    lic["last_verified_at"] = iso(now())
    save_license(lic)


def find_stamp(paths: list[Path]) -> dict | None:
    for start in paths:
        start = start.resolve()
        for d in [start] + list(start.parents):
            f = d / PROJECT_STAMP
            if f.is_file():
                try:
                    return json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    return None
    return None


def check_for_work(argv: list[str]) -> None:
    """The gate's decision. Raises LicenseError with a message a person can act on."""
    lic = load_license()
    if not lic and (SKILL_ROOT / ".git").exists():
        try:
            lic = activate_github()
            print("Licensed through your GitHub access to film-shoot.", file=sys.stderr)
        except LicenseError:
            lic = None
    if not lic:
        raise LicenseError(f"The post tools need an active license. Run "
                           f"`license_check.py activate <key>` — buy one at {BUY_URL}. "
                           f"(Offload, QC, the day sheets and the on-set LUT never need one.)")
    if lic["tier"] != "per-project":
        check_active(lic)
        return
    # Per-project: the work has to be inside a started project, found from the working directory
    # or from any path the tool was given.
    candidates = [Path.cwd()] + [Path(a) for a in argv if a and not a.startswith("-")]
    stamp = find_stamp(candidates)
    if not stamp or stamp.get("key_fingerprint") != fingerprint(lic["key"]):
        raise LicenseError("A per-project license covers work inside a started project. Run "
                           "`license_check.py start-project <shoot folder>` once for this shoot, "
                           "then run this from inside that folder.")


def gate(tool: str, argv: list[str] | None = None, free_flags: tuple[str, ...] = ()) -> None:
    """Call first thing under `if __name__ == "__main__":` in a post tool. Exits 3 on a 'no'."""
    argv = sys.argv[1:] if argv is None else argv
    if any(f in argv for f in ("--selftest", "-h", "--help") + tuple(free_flags)):
        return
    name = Path(tool).name
    try:
        check_for_work(argv)
    except LicenseError as e:
        print(f"{name}: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:                                   # noqa: BLE001 — deliberate
        # 🔴 A BUG IN HERE MUST NOT BECOME A STACK TRACE IN FRONT OF A LICENSED USER.
        #
        # This used to catch LicenseError only, so anything else — a hand-edited license.json, a
        # half-written one, a KeyError from a record this file never produces — surfaced as a
        # traceback. Reproduced with `{"kind":"github"}`: `KeyError: 'tier'`. Someone who has paid
        # reads that as "the software is broken", and has no idea one command fixes it.
        #
        # It still refuses rather than failing open: garbage in license.json must not be a bypass,
        # and a person can clear this in a second once told where to look. Never silent, never a
        # traceback, always actionable.
        print(f"{name}: the license record could not be read ({type(e).__name__}: {e}).\n"
              f"  Delete {license_path()} and re-activate — nothing is lost by doing so.\n"
              f"  (Offload, QC, the day sheets and the on-set LUT never need a license.)",
              file=sys.stderr)
        sys.exit(3)


def status() -> str:
    lic = load_license()
    if not lic:
        return f"No license active on this computer. Buy one at {BUY_URL}."
    who = f" for {lic['holder']}" if lic.get("holder") else ""
    if lic["kind"] == "github":
        head = f"License: friend, through GitHub access to {GITHUB_REPO}"
    else:
        head = f"License: {lic['tier']}{who}, key ...{lic['key'][-8:]}"
    lines = [f"{head}, active since {lic['activated_at'][:10]}."]
    try:
        if lic["tier"] != "per-project":
            check_active(lic)
        lines.append("Status: good for new work.")
    except LicenseError as e:
        lines.append(f"Status: {e}")
    return "\n".join(lines)


# ---- selftest ----------------------------------------------------------------------------------
def selftest() -> int:
    """Every branch above against a fake store and RFC 8032 vectors. Needs no network."""
    import tempfile

    global _verify, _github, _ls_remote, COMP_PUBLIC_KEY
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        print(f"  {'ok ' if cond else 'FAIL'} {name}")

    # 1. Ed25519 against RFC 8032 §7.1 TEST 1 and TEST 2 — an independent source, not our own
    #    signer, so a bug in the maths cannot certify itself.
    t1_pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    t1_sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
                           "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    t2_pub = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    t2_sig = bytes.fromhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
                           "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    check("ed25519: RFC 8032 test 1 verifies", ed25519_verify(t1_pub, b"", t1_sig))
    check("ed25519: RFC 8032 test 2 verifies", ed25519_verify(t2_pub, b"\x72", t2_sig))
    check("ed25519: wrong message rejected", not ed25519_verify(t2_pub, b"\x73", t2_sig))
    check("ed25519: wrong key rejected", not ed25519_verify(t1_pub, b"\x72", t2_sig))
    check("ed25519: flipped signature bit rejected",
          not ed25519_verify(t2_pub, b"\x72", bytes([t2_sig[0] ^ 1]) + t2_sig[1:]))

    # 2. Comp tokens. Signed here with the RFC test-1 secret key (public knowledge, test only) by
    #    a minimal RFC 8032 signer, then verified against the matching public key.
    t1_secret = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")

    def sign(secret: bytes, msg: bytes) -> bytes:
        h = hashlib.sha512(secret).digest()
        a = int.from_bytes(h[:32], "little")
        a &= (1 << 254) - 8
        a |= 1 << 254
        r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), "little") % _Q

        def enc(pt):
            zi = pow(pt[2], _P - 2, _P)
            x, y = pt[0] * zi % _P, pt[1] * zi % _P
            return (y | ((x & 1) << 255)).to_bytes(32, "little")
        R = enc(_mul(r, _G))
        A = enc(_mul(a, _G))
        k = int.from_bytes(hashlib.sha512(R + A + msg).digest(), "little") % _Q
        return R + ((r + k * a) % _Q).to_bytes(32, "little")

    check("signer reproduces RFC 8032 test 1 exactly", sign(t1_secret, b"") == t1_sig)

    def b64(b):
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    def token(payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"fscomp1.{b64(body)}.{b64(sign(t1_secret, body))}"

    good = token({"name": "Test Friend", "scope": "perpetual"})
    check("comp: valid token accepted", read_comp(good, t1_pub)["name"] == "Test Friend")
    for name, bad in [
        ("comp: token rejected by the production key", good),
        ("comp: tampered payload rejected", "fscomp1." + b64(b'{"name":"Someone Else"}') + "."
         + good.split(".")[2]),
        ("comp: truncated token rejected", good[:-10]),
        ("comp: expired token rejected", token({"name": "X", "expires": "2000-01-01"})),
    ]:
        key = t1_pub if "production" not in name else COMP_PUBLIC_KEY
        try:
            read_comp(bad, key)
            check(name, False)
        except LicenseError:
            check(name, True)

    # 3. The gate, against a fake store, in a throwaway FILM_SHOOT_HOME.
    store = {}

    def fake_verify(product_id, key, increment):
        if store.get("down"):
            raise StoreUnreachable("simulated outage")
        rec = store.get((product_id, key))
        if rec is None:
            return {"success": False}
        if increment:
            rec["uses"] += 1
        return {"success": True, "uses": rec["uses"], "purchase": dict(rec["purchase"])}

    real_verify = _verify
    _verify = fake_verify
    github = {"access": False}
    real_github = _github
    _github = lambda: github["access"]
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "home")
        shoot = Path(tmp) / "shoot-a"

        def refused(fn, *a):
            try:
                fn(*a)
                return False
            except LicenseError:
                return True

        check("gate: no license refuses post work", refused(check_for_work, []))
        check("gate: --selftest always exempt", gate("x.py", ["--selftest"]) is None)

        # per-project, quantity 2
        store[(PRODUCT_PER_PROJECT, "PP-KEY")] = {"uses": 0, "purchase": {"quantity": 2}}
        activate("PP-KEY")
        check("per-project: activation uses no project", store[(PRODUCT_PER_PROJECT, "PP-KEY")]["uses"] == 0)
        check("per-project: refused outside a project", refused(check_for_work, [str(Path(tmp))]))
        start_project(shoot)
        check("per-project: works inside the started project", not refused(check_for_work, [str(shoot / "cards")]))
        store["down"] = True
        check("per-project: works offline inside a started project", not refused(check_for_work, [str(shoot)]))
        check("per-project: new project offline refused, not counted", refused(start_project, Path(tmp) / "shoot-x"))
        store["down"] = False
        start_project(shoot)
        check("per-project: restarting the same project costs nothing", store[(PRODUCT_PER_PROJECT, "PP-KEY")]["uses"] == 1)
        start_project(Path(tmp) / "shoot-b")
        check("per-project: third project over quantity 2 refused", refused(start_project, Path(tmp) / "shoot-c"))

        # annual, creator: one computer
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "home2")
        store[(PRODUCT_ANNUAL, "AN-KEY")] = {"uses": 0, "purchase": {"variants": "(Creator)", "quantity": 1}}
        activate("AN-KEY")
        check("creator: works anywhere once active", not refused(check_for_work, [str(Path(tmp))]))
        activate("AN-KEY")
        check("creator: re-activating the same computer is free", store[(PRODUCT_ANNUAL, "AN-KEY")]["uses"] == 1)
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "home3")      # a second computer
        check("creator: second computer refused", refused(activate, "AN-KEY"))

        # annual, studio: ten computers
        store[(PRODUCT_ANNUAL, "ST-KEY")] = {"uses": 0, "purchase": {"variants": "(Studio)", "quantity": 1}}
        for i in range(10):
            os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / f"studio{i}")
            activate("ST-KEY")
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "studio10")
        check("studio: eleventh computer refused", refused(activate, "ST-KEY"))

        # annual lapse, grace, and outage
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "home2")
        lic = load_license()
        lic["last_verified_at"] = iso(now() - _dt.timedelta(days=3))
        save_license(lic)
        store["down"] = True
        check("annual: 3 days offline still works", not refused(check_for_work, []))
        lic["last_verified_at"] = iso(now() - _dt.timedelta(days=8))
        save_license(lic)
        check("annual: 8 days offline refused", refused(check_for_work, []))
        store["down"] = False
        store[(PRODUCT_ANNUAL, "AN-KEY")]["purchase"]["subscription_ended_at"] = "2026-01-01"
        check("annual: ended subscription refused", refused(check_for_work, []))
        store[(PRODUCT_ANNUAL, "AN-KEY")]["purchase"]["subscription_ended_at"] = None
        store[(PRODUCT_ANNUAL, "AN-KEY")]["purchase"]["subscription_cancelled_at"] = "2026-09-01"
        check("annual: cancelled but paid-up still works", not refused(check_for_work, []))

        # refunds and unknown keys
        store[(PRODUCT_PER_PROJECT, "RF-KEY")] = {"uses": 0, "purchase": {"refunded": True}}
        check("refunded purchase refused", refused(activate, "RF-KEY"))
        check("unknown key refused", refused(activate, "NOPE-NOPE"))

        # github: friends licensed by read access to the private repo
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "home-gh")
        check("github: no access, no license -> refused", refused(check_for_work, []))
        github["access"] = True
        check("github: access auto-activates a friend license", not refused(check_for_work, []))
        check("github: recorded as a github license", load_license()["kind"] == "github")
        lic = load_license()
        lic["last_verified_at"] = iso(now() - _dt.timedelta(days=2))
        save_license(lic)
        github["access"] = False
        check("github: access removed -> refused at the recheck", refused(check_for_work, []))
        github["access"] = True
        activate("--github")
        lic = load_license()
        lic["last_verified_at"] = iso(now() - _dt.timedelta(days=20))
        save_license(lic)
        github["access"] = None
        check("github: 20 days unable to reach GitHub still works", not refused(check_for_work, []))
        lic["last_verified_at"] = iso(now() - _dt.timedelta(days=31))
        save_license(lic)
        check("github: 31 days unable to reach GitHub refused", refused(check_for_work, []))
        github["access"] = False

        # github_access() itself, against a fake `git ls-remote`
        real_ls = _ls_remote
        def fake_ls(answers):
            def ls(url, authenticated):
                return answers["anon" if not authenticated else ("ssh" if url.startswith("git@") else "https")]
            return ls
        for name, answers, want in [
            ("github_access: private + credentials -> True", {"anon": "denied", "https": "ok", "ssh": "denied"}, True),
            ("github_access: ssh-only credentials -> True", {"anon": "denied", "https": "denied", "ssh": "ok"}, True),
            ("github_access: repo made PUBLIC -> False", {"anon": "ok", "https": "ok", "ssh": "ok"}, False),
            ("github_access: no credentials -> False", {"anon": "denied", "https": "denied", "ssh": "denied"}, False),
            ("github_access: offline -> None", {"anon": "unreachable", "https": "unreachable", "ssh": "unreachable"}, None),
            ("github_access: flaky, never read as 'no' -> None", {"anon": "denied", "https": "unreachable", "ssh": "denied"}, None),
        ]:
            _ls_remote = fake_ls(answers)
            check(name, github_access() is want)
        _ls_remote = real_ls

        # _classify(), on stderr text captured from real git runs
        for name, err, want in [
            ("classify: https repo not found -> denied",
             "remote: Repository not found.\nfatal: repository 'https://github.com/x/y.git/' not found", "denied"),
            ("classify: ssh not set up -> denied",
             "Host key verification failed.\nfatal: Could not read from remote repository.", "denied"),
            ("classify: ssh with no network -> unreachable",
             "ssh: Could not resolve hostname github.com: No such host is known.\n"
             "fatal: Could not read from remote repository.", "unreachable"),
            ("classify: https with no network -> unreachable",
             "fatal: unable to access 'https://github.com/x/y.git/': Failed to connect to github.com port 443", "unreachable"),
            ("classify: unknown error -> unreachable, never 'no'", "fatal: something new", "unreachable"),
        ]:
            check(name, _classify(err) == want)

        # comp, with the production key swapped for the test key
        os.environ["FILM_SHOOT_HOME"] = str(Path(tmp) / "home-comp")
        prod_key, COMP_PUBLIC_KEY = COMP_PUBLIC_KEY, t1_pub
        try:
            store["down"] = True
            activate(good)
            check("comp: activates with the store down", load_license()["tier"] == "comp")
            check("comp: post work allowed with the store down", not refused(check_for_work, []))
        finally:
            COMP_PUBLIC_KEY = prod_key
            store["down"] = False
        os.environ.pop("FILM_SHOOT_HOME", None)
    _verify = real_verify
    _github = real_github

    # ---- a broken license record must REFUSE, never traceback --------------------------------
    #
    # The gate used to catch LicenseError only, so a record this file never writes -- a hand-edited
    # or half-written one -- came out as a stack trace in front of someone who had paid. These
    # assert the two halves that matter: the process still exits 3 (refused, not crashed), and the
    # message says where the file is so it can be cleared in one step.
    import contextlib
    import io

    def run_gate() -> tuple[int, str]:
        """(exit code, stderr). 0 means the gate let the tool through."""
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                gate("tool.py", argv=["--build", "x"])
            return 0, err.getvalue()
        except SystemExit as e:
            return int(e.code or 0), err.getvalue()

    # ⚠️ Stub the GitHub check to "no access" for these. Left real, an EMPTY record falls through
    # to auto-activation and the outcome depends on whose machine is running the test and whether
    # it has network — which is not a test, it is a coin toss. The first draft of this block did
    # exactly that and reported four failures that were the test's fault, not the code's.
    _github = lambda: False                                   # noqa: E731

    with tempfile.TemporaryDirectory() as td:
        os.environ["FILM_SHOOT_HOME"] = td

        # A record that PARSES but is malformed reaches check_active and used to raise. These are
        # the cases the catch-all exists for, so the message must name the file to delete.
        for label, blob in (("missing keys", '{"kind":"github"}'),
                            ("wrong types", '{"tier": 7, "key": null}')):
            (Path(td) / "license.json").write_text(blob, encoding="utf-8")
            code, msg = run_gate()
            check(f"malformed record ({label}): refused, not crashed", code == 3)
            check(f"malformed record ({label}): says which file to delete", "license.json" in msg)

        # An EMPTY record is falsy, so it is indistinguishable from no license at all — and being
        # treated as "none" is correct, not a bug. It must refuse with the ordinary buy-a-license
        # message, NOT with the "could not be read" one.
        for label, blob in (("empty object", "{}"), ("a JSON list", "[]")):
            (Path(td) / "license.json").write_text(blob, encoding="utf-8")
            code, msg = run_gate()
            check(f"empty record ({label}): refused as 'no license'", code == 3)
            check(f"empty record ({label}): not reported as unreadable",
                  "could not be read" not in msg and "active license" in msg)
        os.environ.pop("FILM_SHOOT_HOME", None)

    # ---- the catch-all must not be a blanket refusal ------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        os.environ["FILM_SHOOT_HOME"] = td
        # A github friend license verified recently returns from cache without touching the network,
        # which is what makes this a deterministic positive control.
        save_license({"key": f"github:{GITHUB_REPO}", "kind": "github", "tier": "friend",
                      "install_id": "test", "activated_at": iso(now()),
                      "last_verified_at": iso(now()), "last_verified_ok": True})
        code, msg = run_gate()
        check("a well-formed license still passes the gate", code == 0)

        # ---- save_license is atomic ------------------------------------------------------------
        check("save_license leaves no .partial behind",
              not (Path(td) / "license.json.partial").exists())
        check("save_license wrote a complete, parseable record",
              (load_license() or {}).get("tier") == "friend")
        os.environ.pop("FILM_SHOOT_HOME", None)

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    return 1 if failures else 0


# ---- CLI ---------------------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--selftest":
        return selftest()
    try:
        cmd = argv[0]
        if cmd == "activate" and len(argv) == 2:
            lic = activate(argv[1])
            who = f" for {lic['holder']}" if lic.get("holder") else ""
            print(f"Activated: {lic['tier']} license{who}.")
        elif cmd == "status":
            print(status())
        elif cmd == "start-project" and len(argv) == 2:
            stamp = start_project(Path(argv[1]))
            print(f"Project started: {argv[1]} (id {stamp['project_id'][:8]}).")
        elif cmd == "deactivate":
            license_path().unlink(missing_ok=True)
            print(f"License removed from this computer. To move a seat to another computer, "
                  f"email {SUPPORT}.")
        else:
            print(__doc__)
            return 2
    except LicenseError as e:
        print(e, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
