"""CLI: continue a generation run that failed partway through.

    python -m pipeline.resume <job_id>

Scenes the failed attempt already finished are reused from the job's
work_dir instead of being rendered again — the reason per-scene jobs exist
(a Wan2.2 scene costs about eight minutes).

There are no options: the script, voice sample, music track and animation
settings all come from the job row, so the resumed run finishes the video
that was interrupted rather than a different one.
"""

from __future__ import annotations

import argparse

from pipeline.run import resume_generation_job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_id", type=int, help="Id of the unfinished job (see show-pet)")
    args = parser.parse_args()

    output_path = resume_generation_job(args.job_id)
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
