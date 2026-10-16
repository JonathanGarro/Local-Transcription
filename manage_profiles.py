"""list, inspect, rename, merge, prune, or delete people in the local voice library.

usage:
    uv run manage_profiles.py list
    uv run manage_profiles.py show "Priya"
    uv run manage_profiles.py rename "Katherine" "Kathryn"       # updates every past transcript
    uv run manage_profiles.py merge "Pri" "Priya"            # fold the first person into the second
    uv run manage_profiles.py remove-entry 3f9a1c2b7d
    uv run manage_profiles.py delete "Priya"
"""

import argparse

import pandas as pd

from local_transcription import library, pipeline


# helpers
def person(name):
    person_id = library.find_person(name)
    if not person_id:
        raise SystemExit(f"no person named {name}")
    return person_id


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="one row per person")
    show = sub.add_parser("show", help="every entry for one person, with similarity to the mean")
    show.add_argument("name")
    rename = sub.add_parser("rename", help="rename a person everywhere")
    rename.add_argument("name")
    rename.add_argument("new_name")
    merge = sub.add_parser("merge", help="merge the first person into the second")
    merge.add_argument("source")
    merge.add_argument("target")
    remove = sub.add_parser("remove-entry", help="delete one entry by id")
    remove.add_argument("entry_id")
    delete = sub.add_parser("delete", help="delete a person and all their voice entries")
    delete.add_argument("name")
    args = parser.parse_args()

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        if args.command == "list":
            people = library.people_summary()
            if people.empty:
                print("no people yet")
                return
            people["profile_seconds"] = people["profile_seconds"].round()
            print(people.drop(columns=["person_id", "created"]).to_string(index=False, na_rep=""))
        elif args.command == "show":
            table = library.entry_similarities(person(args.name))
            if table.empty:
                print(f"{args.name} has no voice entries")
                return
            print(table.drop(columns=["person_id", "name"]).to_string(index=False, na_rep=""))
            print("entries well below the others may be mislabeled, remove them with remove-entry")
        elif args.command == "rename":
            pipeline.rename_person(person(args.name), args.new_name)
            print(f"renamed {args.name} to {args.new_name} and updated their transcripts")
        elif args.command == "merge":
            pipeline.merge_people(person(args.source), person(args.target))
            print(f"merged {args.source} into {args.target} and updated their transcripts")
        elif args.command == "remove-entry":
            name = library.remove_entry(args.entry_id)
            print(f"removed {args.entry_id} from {name}" if name else f"no entry {args.entry_id}")
        elif args.command == "delete":
            library.delete_person(person(args.name))
            print(f"deleted {args.name} and their voice entries, past transcripts keep the name as text")


if __name__ == "__main__":
    main()
