"""list, inspect, rename, merge, prune, or delete people in the local voice library.

usage:
    uv run manage_profiles.py list
    uv run manage_profiles.py show "Priya"
    uv run manage_profiles.py rename "Katherine" "Kathryn"       # updates every past transcript
    uv run manage_profiles.py merge "Pri" "Priya"            # fold the first person into the second
    uv run manage_profiles.py set "Maya" --full-name "Maya Chen" --org "Northwind Foundation" --team "Operations Team"
    uv run manage_profiles.py projects                         # list projects
    uv run manage_profiles.py projects --add "Customer Dashboard"
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
    details = sub.add_parser("set", help="set the full name, organization, and teams used in notes")
    details.add_argument("name")
    details.add_argument("--full-name")
    details.add_argument("--org", help="organization, blank to clear")
    details.add_argument("--team", action="append", help="repeat for several teams, replaces the current teams")
    details.add_argument("--no-teams", action="store_true", help="clear every team")
    projects = sub.add_parser("projects", help="list or add projects")
    projects.add_argument("--add")
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
            columns = ["name", "full_name", "organization", "teams", "entries", "profile_seconds",
                       "meetings", "minutes_heard", "last_heard"]
            print(people[columns].to_string(index=False, na_rep=""))
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
        elif args.command == "set":
            person_id = person(args.name)
            teams = [] if args.no_teams else args.team
            library.set_person_details(person_id, full_name=args.full_name, organization=args.org, teams=teams)
            pipeline.rerender_people([person_id])
            details = library.person_details(person_id)
            print(f"{details['full_name']}, {details['organization'] or 'no organization'}, "
                  f"teams: {', '.join(details['teams']) or 'none'}")
        elif args.command == "projects":
            if args.add:
                library.ensure_project(args.add)
            print("\n".join(library.lookup_names("projects")) or "no projects yet")
        elif args.command == "remove-entry":
            name = library.remove_entry(args.entry_id)
            print(f"removed {args.entry_id} from {name}" if name else f"no entry {args.entry_id}")
        elif args.command == "delete":
            library.delete_person(person(args.name))
            print(f"deleted {args.name} and their voice entries, past transcripts keep the name as text")


if __name__ == "__main__":
    main()
