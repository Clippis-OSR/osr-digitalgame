import json
import subprocess
from pathlib import Path

ROOT = Path("tests/fixtures/replays")

def run(cmd, cwd=None):
    print("\n>", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)

def main():
    replay_files = sorted(ROOT.rglob("replay.json"))
    if not replay_files:
        raise SystemExit(f"No replay.json files found under {ROOT}")

    for replay_path in replay_files:
        fixture_dir = replay_path.parent
        print(f"\n=== Regenerating: {fixture_dir} ===")

        data = json.loads(replay_path.read_text(encoding="utf-8"))
        cmds = data.get("commands") or []
        if not cmds:
            print(f"WARNING: no commands in {replay_path}, skipping.")
            continue

        # Keep these if your schema has them; otherwise defaults are fine.
        party = data.get("party", "default")
        dice_seed = data.get("dice_seed", 11111)
        wild_seed = data.get("wild_seed", 22222)

        for cmd_obj in cmds:
            cmd_json = json.dumps(cmd_obj, separators=(",", ":"))
            run([
                "python", "-m", "scripts.make_fixture",
                str(fixture_dir),
                "--party", str(party),
                "--dice-seed", str(dice_seed),
                "--wild-seed", str(wild_seed),
                "--cmd", cmd_json,
            ])

        run(["python", "-m", "scripts.bless_fixture", str(fixture_dir)])

    print("\nAll fixtures regenerated + blessed.")

if __name__ == "__main__":
    main()