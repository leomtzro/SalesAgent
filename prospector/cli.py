"""Command line interface.

    python -m prospector doctor
    python -m prospector find "Acme Supplements" --domain acmesupplements.com
    python -m prospector batch config/targets.csv --limit 10
    python -m prospector list
    python -m prospector show acme-supplements-jane-doe
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import store
from .client import MissingAPIKey, build_client, load_dotenv
from .offer import OfferConfigError, load_offer
from .pipeline import Target, load_targets, run_batch, run_one

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m",
)


def _color(enabled: bool):
    if enabled:
        return GREEN, RED, YELLOW, DIM, BOLD, RESET
    return "", "", "", "", "", ""


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the setup before spending money on an API call."""
    g, r, y, dim, b, x = _color(not args.no_color)
    load_dotenv()
    ok = True

    print(f"{b}Checking your setup{x}\n")

    # 1. Credentials
    if os.environ.get("ANTHROPIC_API_KEY"):
        print(f"  {g}✓{x} ANTHROPIC_API_KEY is set")
    elif os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print(f"  {g}✓{x} ANTHROPIC_AUTH_TOKEN is set")
    elif os.path.isdir(os.path.expanduser("~/.config/anthropic")):
        print(f"  {g}✓{x} an `ant auth login` profile exists")
    else:
        ok = False
        print(f"  {r}✗{x} No API key found.")
        print(f"      {dim}cp .env.example .env{x} then put your key in it.")
        print(f"      {dim}Get one: https://console.anthropic.com/settings/keys{x}")

    # 2. Dependencies
    try:
        import anthropic  # noqa: F401

        print(f"  {g}✓{x} anthropic SDK installed")
    except ImportError:
        ok = False
        print(f"  {r}✗{x} anthropic SDK missing - run: pip install -r requirements.txt")

    try:
        import dns.resolver  # noqa: F401

        from .contact import dns_is_trustworthy

        # The probe logs a warning of its own, which is right during a real
        # run but duplicates what we print here.
        _dns_log = logging.getLogger("prospector")
        _was = _dns_log.level
        _dns_log.setLevel(logging.ERROR)
        try:
            dns_ok = dns_is_trustworthy()
        finally:
            _dns_log.setLevel(_was)

        if dns_ok:
            print(f"  {g}✓{x} DNS works - email domains will be MX-checked")
        else:
            print(
                f"  {y}!{x} DNS can't resolve known-good domains here, so MX "
                f"checks are off."
            )
            print(
                f"      {dim}Not fatal: addresses just won't be domain-verified. "
                f"Usually a locked-down network or a DNS filter.{x}"
            )
    except ImportError:
        print(
            f"  {y}!{x} dnspython missing - MX checks will be skipped. "
            f"{dim}pip install dnspython{x}"
        )

    # 3. Offer config
    try:
        offer = load_offer(args.offer)
        print(f"  {g}✓{x} {args.offer} loads")
        unfilled = offer.unfilled_placeholders()
        if unfilled:
            ok = False
            print(
                f"  {r}✗{x} still has template placeholders: "
                f"{', '.join(unfilled)}"
            )
            print(f"      {dim}Edit {args.offer} - this is what the agent sells.{x}")
        else:
            print(f"  {g}✓{x} offer config is filled in")
        print(f"      {dim}Selling: {offer.what_you_sell.strip()[:70]}...{x}")
        print(f"      {dim}Looking for: {', '.join(offer.target_titles[:3])}{x}")
        if not offer.proof_points:
            print(
                f"  {y}!{x} no proof_points set - outreach will make no results "
                f"claims (that's intentional, but weaker copy)"
            )
    except OfferConfigError as exc:
        ok = False
        print(f"  {r}✗{x} {exc}")

    # 4. Model
    model = os.environ.get("PROSPECTOR_MODEL", "claude-opus-5-5")
    print(f"  {g}✓{x} model: {model}")

    if not args.live:
        print()
        if ok:
            print(
                f"{g}Looks right.{x} Now prove it against the real API:\n"
                f"  {b}python -m prospector doctor --live{x}  "
                f"{dim}(one search, a fraction of a cent){x}"
            )
            return 0
        print(f"{r}Fix the above, then run doctor again.{x}")
        return 1

    # --- live check ---------------------------------------------------------
    if not ok:
        print(f"\n{r}Fix the above before running --live.{x}")
        return 1

    print(f"\n{b}Live check{x} {dim}(one real API call with web search){x}")
    try:
        from .client import build_client, preflight

        out = preflight(build_client())
    except MissingAPIKey as exc:
        print(f"  {r}✗{x} {exc}")
        return 2
    except Exception as exc:
        name = type(exc).__name__
        print(f"  {r}✗{x} {name}: {exc}")
        if "authentication" in str(exc).lower() or name == "AuthenticationError":
            print(f"      {dim}The key was rejected. Check it in .env.{x}")
        elif name == "NotFoundError":
            print(
                f"      {dim}Model {os.environ.get('PROSPECTOR_MODEL', 'claude-opus-5-5')} "
                f"isn't available to this key. Set PROSPECTOR_MODEL in .env.{x}"
            )
        elif name == "PermissionDeniedError":
            print(f"      {dim}Key lacks access. Check your console settings.{x}")
        return 1

    print(f"  {g}✓{x} API key accepted")
    print(f"  {g}✓{x} model {out['model']} responded")

    if out["tool_errors"]:
        ok = False
        print(f"  {r}✗{x} web search errored: {', '.join(out['tool_errors'])}")
        print(
            f"      {dim}If this says max_uses_exceeded or similar, web search "
            f"may not be enabled for your account.{x}"
        )
    elif out["searched"] and out["search_results"]:
        print(
            f"  {g}✓{x} web search ran and returned "
            f"{out['search_results']} result(s)"
        )
    elif out["searched"]:
        print(f"  {y}!{x} search ran but returned nothing - unusual, retry once")
    else:
        ok = False
        print(f"  {r}✗{x} the model never called web search")
        print(
            f"      {dim}Without search this agent cannot research anything. "
            f"Check web search is enabled for your org.{x}"
        )

    if out["answer"]:
        print(f"      {dim}It said: {out['answer'][:90]}{x}")
    print(
        f"      {dim}cost: {out['input_tokens']:,} in / "
        f"{out['output_tokens']:,} out tokens{x}"
    )

    print()
    if ok:
        print(
            f"{g}Working.{x} Go: "
            f"{b}python -m prospector find \"Some Company\"{x}"
        )
        return 0
    print(f"{r}The live check found a problem - see above.{x}")
    return 1


def _print_result(result, *, no_color: bool = False) -> None:
    g, r, y, dim, b, x = _color(not no_color)
    if not result.ok:
        print(f"  {r}✗ {result.target.company}{x}: {result.error}")
        return

    p = result.prospect
    email = p.best_email
    phone = p.best_phone
    score = p.fit_score
    tint = g if score >= 70 else (y if score >= 40 else dim)

    print(f"  {g}✓{x} {b}{p.company.name}{x} {tint}[fit {score}]{x}")

    ident = p.person.identity_confidence.value
    if p.person.full_name:
        tag = {
            "confirmed": "",
            "aggregator_only": f" {y}(directory only){x}",
            "unknown": f" {y}(unsourced){x}",
        }.get(ident, f" {y}({ident}){x}")
        print(f"      {p.person.full_name} - {p.person.title or '?'}{tag}")
    else:
        print(f"      {y}no name{x} - {p.person.title or '?'} {dim}(role confirmed){x}")
        if p.person.ask_switchboard_for:
            print(f"      ask reception for: \"{p.person.ask_switchboard_for}\"")
    if email:
        flag = "" if email.confidence.value == "verified" else f" {y}({email.confidence.value}){x}"
        print(f"      {email.address}{flag}")
    else:
        print(f"      {y}no email found{x}")
    if phone:
        print(f"      {phone.number} ({phone.kind})")
    if p.signals:
        print(f"      {dim}why now: {p.signals[0].signal[:70]}{x}")
    for warning in result.warnings:
        print(f"      {y}! {warning}{x}")
    for item in result.lint:
        print(f"      {y}! draft: {item}{x}")
    if result.brief_path:
        print(f"      {dim}{result.brief_path}{x}")


def cmd_find(args: argparse.Namespace) -> int:
    g, r, y, dim, b, x = _color(not args.no_color)
    target = Target(company=args.company, domain=args.domain, notes=args.note)
    print(f"Researching {b}{target.company}{x}...")
    result = run_one(
        target,
        offer_path=args.offer,
        write_outreach_copy=not args.no_copy,
        verified_only=args.verified_only,
        max_searches=args.max_searches,
        keep_notes=not args.no_notes,
        min_fit_for_outreach=args.min_fit,
    )
    print()
    _print_result(result, no_color=args.no_color)
    if result.ok:
        store.write_csv(sorted(store.read_all(), key=lambda p: -p.fit_score))
        stats = result.stats or {}
        print(
            f"\n{dim}{stats.get('searches', 0)} searches, "
            f"{stats.get('fetches', 0)} page fetches, "
            f"{stats.get('input_tokens', 0):,} in / "
            f"{stats.get('output_tokens', 0):,} out tokens{x}"
        )
        if result.brief_path:
            print(f"\nRead the brief: {b}{result.brief_path}{x}")
    return 0 if result.ok else 1


def cmd_batch(args: argparse.Namespace) -> int:
    g, r, y, dim, b, x = _color(not args.no_color)
    targets = load_targets(args.targets)
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("No targets to research.")
        return 0

    print(f"Researching {b}{len(targets)}{x} companies, {args.concurrency} at a time...\n")
    results = run_batch(
        targets,
        concurrency=args.concurrency,
        skip_existing=not args.redo,
        on_result=lambda res: _print_result(res, no_color=args.no_color),
        offer_path=args.offer,
        write_outreach_copy=not args.no_copy,
        verified_only=args.verified_only,
        max_searches=args.max_searches,
        keep_notes=not args.no_notes,
        min_fit_for_outreach=args.min_fit,
    )

    good = [res for res in results if res.ok]
    failed = [res for res in results if not res.ok]
    with_email = [
        res for res in good if res.prospect and res.prospect.best_email
    ]
    hot = [res for res in good if res.prospect and res.prospect.fit_score >= 70]

    print(f"\n{b}Done.{x}")
    print(f"  {len(good)}/{len(results)} researched")
    print(f"  {len(with_email)} with an email address")
    print(f"  {len(hot)} scored 70+ (call these first)")
    if failed:
        print(f"  {r}{len(failed)} failed{x}")
    print(f"\n  Pipeline CSV: {b}{store.CSV_PATH}{x}")
    print(f"  Briefs: {b}{store.BRIEF_DIR}/{x}")
    return 0 if good else 1


def cmd_list(args: argparse.Namespace) -> int:
    g, r, y, dim, b, x = _color(not args.no_color)
    prospects = sorted(store.read_all(), key=lambda p: -p.fit_score)
    if not prospects:
        print("Nothing researched yet. Try: python -m prospector find \"Some Company\"")
        return 0
    print(f"{b}{'FIT':>4}  {'COMPANY':<28} {'PERSON':<22} {'EMAIL':<32}{x}")
    for p in prospects:
        if args.min_fit and p.fit_score < args.min_fit:
            continue
        email = p.best_email
        addr = email.address if email else "-"
        mark = "" if (email and email.confidence.value == "verified") else "?"
        tint = g if p.fit_score >= 70 else (y if p.fit_score >= 40 else dim)
        print(
            f"{tint}{p.fit_score:>4}{x}  {p.company.name[:28]:<28} "
            f"{(p.person.full_name or '-')[:22]:<22} {addr[:31]:<31}{mark}"
        )
    print(f"\n{dim}? = unverified address. {len(prospects)} total.{x}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = store.BRIEF_DIR / f"{args.slug}.md"
    if not path.exists():
        matches = sorted(store.BRIEF_DIR.glob(f"*{args.slug}*.md"))
        if len(matches) == 1:
            path = matches[0]
        elif matches:
            print("Several briefs match:")
            for m in matches:
                print(f"  {m.stem}")
            return 1
        else:
            print(f"No brief matching {args.slug!r}. Run `python -m prospector list`.")
            return 1
    print(path.read_text())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prospector",
        description="Find the right person at a company, their contact details, "
        "and a first approach worth sending.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress")
    parser.add_argument("--no-color", action="store_true", help="plain output")
    parser.add_argument(
        "--offer",
        default="config/offer.yaml",
        help="path to your offer config (default: config/offer.yaml)",
    )

    # The same global flags, accepted AFTER the subcommand too, because
    # `prospector doctor --no-color` is what people actually type. SUPPRESS
    # means an omitted flag here leaves the top-level value alone instead of
    # resetting it to False.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="log progress",
    )
    common.add_argument(
        "--no-color", action="store_true", default=argparse.SUPPRESS,
        help="plain output",
    )
    common.add_argument(
        "--offer", default=argparse.SUPPRESS, help="path to your offer config",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    def add_research_flags(sp):
        sp.add_argument(
            "--no-copy", action="store_true", help="research only, don't write outreach"
        )
        sp.add_argument(
            "--verified-only",
            action="store_true",
            help="discard guessed email addresses; keep only published ones",
        )
        sp.add_argument(
            "--max-searches",
            type=int,
            default=None,
            help="cap web searches per company (cost control)",
        )
        sp.add_argument(
            "--no-notes",
            action="store_true",
            help="leave raw research notes out of the brief",
        )
        sp.add_argument(
            "--min-fit",
            type=int,
            default=0,
            help="skip writing outreach below this fit score (default: 0)",
        )

    d = sub.add_parser("doctor", help="check your setup", parents=[common])
    d.add_argument(
        "--live",
        action="store_true",
        help="also make one real API call to prove the key, model and web "
        "search all work (costs a fraction of a cent)",
    )
    d.set_defaults(func=cmd_doctor, live=False)

    f = sub.add_parser("find", help="research one company", parents=[common])
    f.add_argument("company")
    f.add_argument("--domain", default=None, help="their website, if you know it")
    f.add_argument("--note", default=None, help="context you already have")
    add_research_flags(f)
    f.set_defaults(func=cmd_find)

    bt = sub.add_parser("batch", help="research a CSV of companies", parents=[common])
    bt.add_argument("targets", help="CSV with a 'company' column")
    bt.add_argument("--limit", type=int, default=None, help="only the first N")
    bt.add_argument(
        "--concurrency", type=int, default=3, help="companies in parallel (default 3)"
    )
    bt.add_argument(
        "--redo", action="store_true", help="re-research companies already on file"
    )
    add_research_flags(bt)
    bt.set_defaults(func=cmd_batch)

    ls = sub.add_parser("list", help="show everything researched so far", parents=[common])
    ls.add_argument("--min-fit", type=int, default=None, help="only this score and up")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="print one brief", parents=[common])
    sh.add_argument("slug", help="from `list`, or any part of the name")
    sh.set_defaults(func=cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except MissingAPIKey as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except OfferConfigError as exc:
        print(f"\nProblem with your offer config: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
