from __future__ import annotations

import argparse
import json
import sys

from pipeline import pet_repo
from pipeline.db import init_db
from pipeline.profile import PetProfile

# Windows consoles default to the system codepage (e.g. cp950), which cannot
# render Chinese pet names/text — force UTF-8 so output isn't mojibake.
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def cmd_init_db(_args) -> None:
    init_db()
    print("Database tables ready.")


def cmd_import_profile(args) -> None:
    profile = PetProfile.load(args.path)
    pet_repo.save_pet(profile)
    print(f"Imported {profile.pet_id} ({profile.name}) into the pet catalog.")


def cmd_list_pets(_args) -> None:
    pets = pet_repo.list_pets()
    if not pets:
        print("No pets in the catalog yet — use `import-profile` first.")
        return
    for pet in pets:
        print(f"{pet.pet_id}\t{pet.name}\t{pet.species}")


def cmd_show_pet(args) -> None:
    profile = pet_repo.get_pet(args.pet_id)
    if profile is None:
        print(f"No pet found with id {args.pet_id!r}.")
        return

    print(profile.model_dump_json(indent=2))

    jobs = pet_repo.list_generation_jobs(args.pet_id)
    print(f"\n{len(jobs)} generation job(s):")
    for job in jobs:
        print(json.dumps(job, ensure_ascii=False, indent=2))


def cmd_cleanup_jobs(args) -> None:
    """Free the disk a pet's older versions are holding.

    Keeps the newest --keep versions playable and deletes the rendered files
    of the rest. The rows stay either way — what the project requires kept is
    the record of which provider, prompt and script produced each video, not
    the clips.
    """
    jobs = pet_repo.list_generation_jobs(args.pet_id)
    stale = [j for j in jobs[args.keep :] if not j["cleaned_at"] and j["status"] != "running"]
    if not stale:
        print(f"Nothing to clean up for {args.pet_id} (keeping the newest {args.keep}).")
        return

    total = 0
    for job in stale:
        result = pet_repo.cleanup_generation_job(job["id"])
        total += result["bytes_freed"]
        print(f"Cleaned job {job['id']}: freed {result['bytes_freed'] / 1048576:.1f} MB")
    print()
    print(f"Freed {total / 1048576:.1f} MB across {len(stale)} version(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the pet catalog database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create database tables").set_defaults(func=cmd_init_db)

    import_parser = subparsers.add_parser("import-profile", help="Import a Pet Profile JSON file")
    import_parser.add_argument("path")
    import_parser.set_defaults(func=cmd_import_profile)

    subparsers.add_parser("list-pets", help="List all pets in the catalog").set_defaults(
        func=cmd_list_pets
    )

    show_parser = subparsers.add_parser(
        "show-pet", help="Show a pet's profile and generation history"
    )
    show_parser.add_argument("pet_id")
    show_parser.set_defaults(func=cmd_show_pet)

    cleanup_parser = subparsers.add_parser(
        "cleanup-jobs",
        help="Delete the rendered files of a pet's older versions (keeps the records)",
    )
    cleanup_parser.add_argument("pet_id")
    cleanup_parser.add_argument(
        "--keep",
        type=int,
        default=3,
        help="How many of the newest versions to leave playable (default: 3)",
    )
    cleanup_parser.set_defaults(func=cmd_cleanup_jobs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
