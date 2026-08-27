"""Phase O - non-interactive review/approval workflow for recommendations.

    # list everything still pending (not REJECTED/PUBLISHED)
    python scripts/review.py --list

    # move one recommendation through the human-in-the-loop lifecycle
    python scripts/review.py --transition <rec_id> --to REVIEWED --actor alice
    python scripts/review.py --approve <rec_id> --actor alice
    python scripts/review.py --reject <rec_id> --actor alice --note "price too aggressive"
    python scripts/review.py --publish <rec_id> --actor alice

This does not create a human review process - it gives one a tool. No
recommendation in ``artifacts/recommendation_log.csv`` is ever rewritten;
every transition is a new row in ``artifacts/recommendation_transitions.csv``,
and "current state" is the read-time join implemented by
``RecommendationLog.read_with_state``.

Output: artifacts/metrics/review_queue.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.audit import RecommendationLog, RecommendationTransitions  # noqa: E402
from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

TERMINAL_STATES = {"REJECTED", "PUBLISHED"}
LIST_COLUMNS = [
    "rec_id", "lifecycle_state_current", "upc", "store", "decision_week",
    "decision", "risk_level", "final_recommended_price",
]


def _print_queue(pending) -> None:
    if pending.empty:
        print("No pending recommendations.")
        return
    cols = [c for c in LIST_COLUMNS if c in pending.columns]
    print(pending[cols].to_string(index=False))


def cmd_list(log: RecommendationLog, transitions: RecommendationTransitions, cfg) -> int:
    frame = log.read_with_state(transitions)
    pending = frame[~frame["lifecycle_state_current"].isin(TERMINAL_STATES)]
    _print_queue(pending)

    payload = {
        "generated_at_utc": utc_now(),
        "n_pending": int(len(pending)),
        "n_by_state": pending["lifecycle_state_current"].value_counts().to_dict() if len(frame) else {},
        "pending": pending[[c for c in LIST_COLUMNS if c in pending.columns]].to_dict(orient="records"),
    }
    out = cfg.path("metrics_dir") / "review_queue.json"
    write_json(out, payload)
    print(f"\n{payload['n_pending']} pending | wrote {out}")
    return 0


def cmd_transition(
    log: RecommendationLog,
    transitions: RecommendationTransitions,
    rec_id: str,
    to_state: str,
    actor: str | None,
    note: str | None,
) -> int:
    stored = log.read()
    known_ids = set(stored["rec_id"]) if "rec_id" in stored.columns else set()
    try:
        row = transitions.append_transition(
            rec_id, to_state=to_state, actor=actor, note=note, valid_rec_ids=known_ids
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"{row['rec_id']}: {row['from_state']} -> {row['to_state']} (actor={actor!r})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List pending (non-terminal) recommendations.")
    parser.add_argument("--transition", metavar="REC_ID", help="Move REC_ID to --to.")
    parser.add_argument("--approve", metavar="REC_ID", help="Shorthand for --transition REC_ID --to APPROVED.")
    parser.add_argument("--reject", metavar="REC_ID", help="Shorthand for --transition REC_ID --to REJECTED.")
    parser.add_argument("--publish", metavar="REC_ID", help="Shorthand for --transition REC_ID --to PUBLISHED.")
    parser.add_argument("--to", choices=["REVIEWED", "APPROVED", "REJECTED", "PUBLISHED"],
                         help="Target lifecycle state (required with --transition).")
    parser.add_argument("--actor", default=None, help="Who is making this decision.")
    parser.add_argument("--note", default=None, help="Free-text note (required for --reject).")
    args = parser.parse_args()

    cfg = load_config()
    log = RecommendationLog(cfg.path("recommendation_log"))
    transitions = RecommendationTransitions(cfg.path("recommendation_transitions_log"))

    if args.list:
        return cmd_list(log, transitions, cfg)

    if args.approve:
        return cmd_transition(log, transitions, args.approve, "APPROVED", args.actor, args.note)
    if args.publish:
        return cmd_transition(log, transitions, args.publish, "PUBLISHED", args.actor, args.note)
    if args.reject:
        if not args.note:
            parser.error("--reject requires --note explaining why")
        return cmd_transition(log, transitions, args.reject, "REJECTED", args.actor, args.note)
    if args.transition:
        if not args.to:
            parser.error("--transition requires --to")
        return cmd_transition(log, transitions, args.transition, args.to, args.actor, args.note)

    parser.error("supply --list, --transition/--to, --approve, --reject, or --publish")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
