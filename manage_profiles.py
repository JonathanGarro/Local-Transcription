"""list, inspect, prune, or delete local voice profiles.

usage:
    uv run manage_profiles.py list
    uv run manage_profiles.py show "Priya"
    uv run manage_profiles.py remove-entry 3f9a1c2b7d
    uv run manage_profiles.py delete "Priya"
"""

import argparse

import pandas as pd

from local_transcription import profiles


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="one row per person")
    show = sub.add_parser("show", help="every entry for one person, with similarity to the mean")
    show.add_argument("name")
    remove = sub.add_parser("remove-entry", help="delete one entry by id")
    remove.add_argument("entry_id")
    delete = sub.add_parser("delete", help="delete a whole profile")
    delete.add_argument("name")
    args = parser.parse_args()

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        if args.command == "list":
            entries = profiles.all_entries()
            if entries.empty:
                print("no profiles yet")
                return
            summary = entries.groupby("name").agg(entries=("entry_id", "size"),
                                                  seconds=("seconds", "sum"),
                                                  sources=("source_type", lambda s: ", ".join(sorted(set(s)))),
                                                  last_added=("added", "max"))
            print(summary.to_string())
        elif args.command == "show":
            table = profiles.entry_similarities(args.name)
            if table.empty:
                print(f"no profile named {args.name}")
                return
            print(table.drop(columns="name").to_string(index=False, na_rep=""))
            print("entries well below the others may be mislabeled, remove them with remove-entry")
        elif args.command == "remove-entry":
            name = profiles.remove_entry(args.entry_id)
            print(f"removed {args.entry_id} from {name}" if name else f"no entry {args.entry_id}")
        elif args.command == "delete":
            gone = profiles.delete_person(args.name)
            print(f"deleted profile {args.name}" if gone else f"no profile named {args.name}")


if __name__ == "__main__":
    main()
